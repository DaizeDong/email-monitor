"""Keep synthetic tests out of real profiles, logs, state and external services.

Boundaries are installed before collection because modules resolve environment
paths while importing. An autouse fixture would run too late. Generated inputs,
temporary storage and intercepted effects keep test failures separate from
operational diagnostics without reading real runtime state.
"""
import os
import socket
import subprocess
import sys
import tempfile
import types
import importlib.util
import json
import shutil
from pathlib import Path

# Ordinary repository scans use the invoking operator's policy metadata. Runtime
# tests keep the synthetic profile below; only the exact read-only scans restore it.
_SCANNER_PROFILE = {name: os.environ.get(name) for name in ("HOME", "USERPROFILE")}
_SCANNER_GIT = {name: value for name, value in os.environ.items()
                if name.upper().startswith('GIT_')}

_SANDBOX = os.path.join(tempfile.gettempdir(), "email-monitor-tests")
os.makedirs(os.path.join(_SANDBOX, "state"), exist_ok=True)

# setdefault, not assignment: a caller who deliberately points these somewhere
# (a debugging run, CI collecting artifacts) keeps their choice.
os.environ.setdefault("EMAIL_MONITOR_LOG",
                      os.path.join(_SANDBOX, "email-monitor.log"))
os.environ.setdefault("EMAIL_MONITOR_STATE_DIR",
                      os.path.join(_SANDBOX, "state"))

# Install the offline boundary before collection imports production. Tests may
# replace adapters with synthetic fakes; a missing fake must never touch a mailbox.
_SANDBOX = Path(tempfile.mkdtemp(prefix="email-monitor-tests-"))
for variable in ("HOME", "USERPROFILE"):
    os.environ[variable] = str(_SANDBOX)
for variable, relative in {
    "EMAIL_MONITOR_CONFIG_DIR": "absent-config", "EMAIL_MONITOR_CONFIG": "absent-config",
    "EMAIL_MONITOR_STATE_DIR": "state", "EMAIL_MONITOR_LOG": "email-monitor.log",
    "EMAIL_MONITOR_NOTIFIER": "absent-notifier.py", "SCHEDULE_RELAY_PY": "absent-relay.py",
    "EMAIL_MONITOR_LABEL_TOOL": "absent-label.py",
}.items():
    os.environ[variable] = str(_SANDBOX / relative)
os.environ.pop("GMAIL_APP_PW", None)
os.environ.pop("EMAIL_MONITOR_REMINDER_CLI", None)
sys.dont_write_bytecode = True

# Synthetic PRIVATE Git metadata makes ordinary logger tests exercise the actual
# storage proof. The generator, rather than a copied operator account, owns it.
_SOURCE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("email_fixture_generator", _SOURCE / "tools" / "make_fixtures.py")
_generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_generator)
_input = _generator.reliability_cases()
for name, content in _generator.private_repository_fixture().items():
    target = _SANDBOX / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
(_SANDBOX / ".pii-guard").mkdir()
from datetime import datetime, timezone
(_SANDBOX / ".pii-guard" / "visibility.json").write_text(json.dumps({
    **_input["visibility"], '_refreshed': datetime.now(timezone.utc).isoformat(),
}), encoding="utf-8")
for name in list(os.environ):
    if name.upper().startswith('GIT_') or name.upper().endswith('_PROXY'):
        os.environ.pop(name)
os.environ.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                  GIT_CONFIG_SYSTEM=os.devnull, GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0')


def _blocked(*args, **kwargs):
    raise RuntimeError("offline suite: external effect requires a test-local fake")


socket.create_connection = _blocked
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
_real_popen = subprocess.Popen
_REMINDER_PATH = None
_GUARD_SCANNER = _SOURCE / "guards/tools/pii_guard.py"


def pytest_addoption(parser):
    parser.addoption("--reminder-source", help="Copy the real reminder Python modules into the disposable test HOME")
    parser.addoption("--guards-source", help="Use this Guards kit for the original read-only repository scans")


def pytest_configure(config):
    global _REMINDER_PATH, _GUARD_SCANNER
    guards_source = config.getoption("--guards-source")
    if guards_source:
        _GUARD_SCANNER = Path(guards_source).resolve() / "tools/pii_guard.py"
        if not _GUARD_SCANNER.is_file():
            raise ValueError("--guards-source must contain tools/pii_guard.py")
    source = config.getoption("--reminder-source")
    if not source:
        return
    source = Path(source).resolve()
    target = _SANDBOX / "CodesClaude/schedule-reminder/skills/schedule-reminder/scripts"
    target.mkdir(parents=True)
    for name in ("reminder.py", "store.py"):
        if not (source / name).is_file():
            raise ValueError("--reminder-source must contain reminder.py and store.py")
    for script in source.rglob('*.py'):
        destination = target / script.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(script, destination)
    _REMINDER_PATH = target / "reminder.py"


def _native_test_command(command):
    if len(command) < 4 or command[:2] != [sys.executable, "-B"]:
        return False
    script = Path(command[2]).resolve()
    if script == _REMINDER_PATH:
        if len(command) < 8 or command[3] != "--db" or command[5:7] != ["--actor", "email-monitor"]:
            return False
        database = Path(command[4]).resolve()
        return database.is_relative_to(_TEMP_ROOT) and command[7] in {
            "init", "list", "get", "add", "update", "transition", "done", "block"}
    # The bytecode probe is generated in a test directory and only imports a constant.
    return (script.is_relative_to(_TEMP_ROOT) and script.name == "synthetic_owner.py"
            and script.read_text(encoding="utf-8") ==
            'import synthetic_helper\nprint(\'{"ok": true}\')\n'
            and (script.parent / "synthetic_helper.py").read_text(encoding="utf-8") == 'value = 1\n')


def _is_guard_scan(command):
    return (len(command) >= 3
            and Path(str(command[0])).resolve() == Path(sys.executable).resolve()
            and Path(str(command[1])).resolve() == _SOURCE / "guards/tools/pii_guard.py"
            and command[2:] in (["--tree"], ["--tree", "--history"]))


def _read_only_git(command):
    if not command or Path(str(command[0])).stem.lower() != 'git':
        return False
    arguments = command[1:]
    while len(arguments) >= 2 and arguments[0] in ('-C', '-c'):
        if arguments[0] == '-c' and arguments[1] != 'core.excludesFile=' + os.devnull:
            return False
        arguments = arguments[2:]
    if not arguments:
        return False
    if arguments[0] == 'remote':
        return len(arguments) == 1 or (len(arguments) >= 3 and arguments[1] == 'get-url')
    if arguments[0] == 'config':
        return arguments[1:] == ['--null', '--list']
    return arguments[0] in {'rev-parse', 'check-ignore', 'ls-files', 'log', 'show', 'diff'}


class _SafePopen(_real_popen):
    """Preserve Popen's class interface for Windows asyncio subprocess support."""

    def __init__(self, args, *pos, **kw):
        command = list(args) if isinstance(args, (list, tuple)) else []
        read_only_git = _read_only_git(command)
        guard_scan = _is_guard_scan(command)
        if not (read_only_git or guard_scan or _native_test_command(command)):
            _blocked()
        if guard_scan:
            command[1] = str(_GUARD_SCANNER)
            args = command
            env = dict(os.environ if kw.get("env") is None else kw["env"])
            for name in list(env):
                if name.upper().startswith('GIT_'):
                    env.pop(name)
            env.update(_SCANNER_GIT)
            for name, value in _SCANNER_PROFILE.items():
                if value is None:
                    env.pop(name, None)
                else:
                    env[name] = value
            kw["env"] = env
        super().__init__(args, *pos, **kw)


subprocess.Popen = _SafePopen
_TEMP_ROOT = Path(tempfile.gettempdir()).resolve()


llmcall = types.ModuleType("llmcall")
llmcall.call = _blocked
llmcall.active_chain = lambda: ()
sys.modules["llmcall"] = llmcall


_ACTIVE = True


def _file_boundary(event, args):
    if not _ACTIVE:
        return
    if event != "open" or isinstance(args[0], int):
        return
    if str(args[0]).lower() in (os.devnull.lower(), "nul", "\\\\.\\nul"):
        return
    path = Path(args[0]).resolve()
    if any(part in (".email-monitor-config", ".pw-auth", ".credentials.json", ".local")
           for part in path.parts) and not path.is_relative_to(_SANDBOX):
        raise RuntimeError("offline suite: private profile read blocked")
    mode = args[1] or ""
    flags = args[2] or 0
    writing = any(c in mode for c in "wax+") or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)
    if writing and not path.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise RuntimeError("offline suite: writes must stay in temporary test storage")


sys.addaudithook(_file_boundary)


def pytest_sessionfinish(session, exitstatus):
    global _ACTIVE
    _ACTIVE = False
