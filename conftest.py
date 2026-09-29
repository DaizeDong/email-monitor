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
import configparser
from pathlib import Path

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
sys.dont_write_bytecode = True

# Synthetic PRIVATE Git metadata makes ordinary logger tests exercise the actual
# storage proof. The generator, rather than a copied operator account, owns it.
_SOURCE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("email_fixture_generator", _SOURCE / "tools" / "make_fixtures.py")
_generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_generator)
_input = _generator.reliability_cases()
(_SANDBOX / ".git").mkdir()
(_SANDBOX / ".git" / "config").write_text(
    '[remote "origin"]\nurl = ' + _input["private_remote"], encoding="utf-8")
(_SANDBOX / ".pii-guard").mkdir()
(_SANDBOX / ".pii-guard" / "visibility.json").write_text(json.dumps(_input["visibility"]), encoding="utf-8")


def _blocked(*args, **kwargs):
    raise RuntimeError("offline suite: external effect requires a test-local fake")


socket.create_connection = _blocked
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
_real_popen = subprocess.Popen


class _SafePopen(_real_popen):
    """Preserve Popen's class interface for Windows asyncio subprocess support."""

    def __init__(self, args, *pos, **kw):
        command = list(args) if isinstance(args, (list, tuple)) else []
        read_only_git = command and Path(str(command[0])).stem.lower() == "git" and any(
            item in command for item in ("rev-parse", "check-ignore", "ls-files", "log", "show", "diff")
        )
        guard_scan = len(command) > 1 and Path(str(command[1])).resolve() == _SOURCE / "guards" / "tools" / "pii_guard.py"
        if not (read_only_git or guard_scan):
            _blocked()
        super().__init__(args, *pos, **kw)


subprocess.Popen = _SafePopen
_real_run = subprocess.run
_TEMP_ROOT = Path(tempfile.gettempdir()).resolve()


def _offline_metadata_run(args, *pos, **kw):
    """Supply Git/gh transport for generated repositories, never ambient metadata."""
    command = list(args) if isinstance(args, (list, tuple)) else []
    if len(command) >= 5 and command[:2] == ['git', '-C']:
        requested = Path(command[2]).resolve()
        if requested.is_relative_to(_TEMP_ROOT):
            if command[3:] == ['-c', 'core.excludesFile=' + os.devnull,
                               'check-ignore', '--no-index', '--verbose', '-z', '--stdin']:
                return _real_run(args, *pos, **kw)
            root = next((p for p in (requested, *requested.parents)
                         if p.is_relative_to(_TEMP_ROOT) and (p/'.git').exists()), None)
            try:
                if root is None:
                    raise ValueError('synthetic directory is unversioned')
                metadata = root/'.git'
                if metadata.is_file():
                    pointer = metadata.read_text(encoding='utf-8')
                    if not pointer.startswith('gitdir:'):
                        raise ValueError('invalid synthetic worktree pointer')
                    metadata = (root/pointer.split(':', 1)[1].strip()).resolve()
                if not metadata.is_relative_to(_TEMP_ROOT):
                    raise ValueError('synthetic worktree escaped test storage')
                if (metadata/'commondir').is_file():
                    metadata = (metadata/(metadata/'commondir').read_text().strip()).resolve()
                if not metadata.is_relative_to(_TEMP_ROOT):
                    raise ValueError('synthetic common directory escaped test storage')
                parser = configparser.ConfigParser(interpolation=None)
                parser.read_string((metadata/'config').read_text(encoding='utf-8'))
                remote = parser.get('remote "origin"', 'url')
                if command[3:] == ['rev-parse', '--show-toplevel']:
                    return subprocess.CompletedProcess(args, 0, str(root), '')
                if command[3:] == ['remote', 'get-url', 'origin']:
                    return subprocess.CompletedProcess(args, 0, remote, '')
            except (OSError, ValueError, configparser.Error):
                return subprocess.CompletedProcess(args, 128, '', 'invalid synthetic Git repository')
            return _blocked()
    if command[:3] == ['gh', 'repo', 'view']:
        slug = _input['private_proof']['slug']
        if len(command) == 6 and command[3] == slug and command[4:] == ['--json', 'nameWithOwner,visibility']:
            return subprocess.CompletedProcess(args, 0, json.dumps({'nameWithOwner': slug, 'visibility': 'PRIVATE'}), '')
        return _blocked()
    return _real_run(args, *pos, **kw)


subprocess.run = _offline_metadata_run
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
