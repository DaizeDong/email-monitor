"""Runtime configuration, DATA path checks, the state writer lock and atomic publication."""
from contextlib import contextmanager
import datetime
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import subprocess
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[3]


def _resolved_companion_root():
    """Use the installed fleet-guards discovery convention for the private repository root."""
    try:
        from fleet_guards.runtime import companion_resolver
    except ImportError as exc:
        raise ImportError('fleet_guards is not installed; run: pip install -e "$(git config --get fleet.guards)"') from exc
    return companion_resolver(source_root=SOURCE_ROOT).resolve_companion_root('email-monitor')


def companion_dir(explicit=None):
    """Honor explicit selections, including missing paths, before shared discovery."""
    selected = explicit or os.environ.get('EMAIL_MONITOR_CONFIG') or os.environ.get('EMAIL_MONITOR_CONFIG_DIR')
    if selected:
        return Path(selected).expanduser().absolute()
    resolved = _resolved_companion_root()
    return Path(resolved).absolute() if resolved else None


def companion_file(name):
    root = companion_dir()
    return str(root / name) if root is not None else None


def valid_account_slug(slug):
    """Accept one portable account state filename component."""
    return (isinstance(slug, str) and re.fullmatch(r"[A-Za-z0-9_.@+-]+", slug) is not None
            and slug not in (".", "..")
            and re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", slug, re.I) is None)


def _plain_path(destination):
    """Preserve lexical components until filesystem aliases have been rejected."""
    path = Path(destination).expanduser().absolute()
    if any(part.casefold() == '.git' or part == '..' for part in path.parts):
        raise ValueError('DATA cannot traverse parent components or Git metadata')
    if any(':' in part for part in path.parts[1:]):
        raise ValueError('DATA cannot use alternate filesystem streams')
    for component in (path, *path.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & 0x400):
            raise ValueError('DATA path cannot contain filesystem aliases')
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError('DATA file cannot have multiple hard links')
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError('DATA requires a regular file or directory')
    if path.is_relative_to(SOURCE_ROOT) or SOURCE_ROOT.is_relative_to(path):
        raise ValueError('DATA destination is inside the public source tree')
    return path


def private_path(destination):
    """Return ``destination`` as an absolute path once the local DATA path checks pass.

    These are filesystem checks only and start no process: no parent traversal, Git metadata,
    alternate streams, links, junctions or hard links; not inside this public source tree; and
    inside a Git worktree (the PRIVATE companion), so DATA stays versioned. Repository visibility
    is not checked at runtime.
    """
    path = _plain_path(destination)
    containing = path if path.is_dir() else path.parent
    root = next((directory for directory in (containing, *containing.parents)
                 if os.path.lexists(directory / '.git')), None)
    if root is None:
        raise ValueError('DATA destination requires a PRIVATE Git companion')
    if SOURCE_ROOT.is_relative_to(root):
        raise ValueError('DATA requires a separate PRIVATE worktree')
    return path


# One writer per state directory. Every process that rewrites account or summary state holds this
# lock for its whole run: the tick, em_catchup, the standalone em_watch CLI and a hand-started
# em_summary. The summary worker a tick starts works under the tick's lock instead (it checks the
# lock is held by the tick that is its parent). The OS releases it when the holder exits, so a crash never leaves it stale. Byte 0
# is the lock; the holder's role, pid and start time follow it so a refused writer can name it.
WRITER_LOCK = '.writer.lock'


class WriterBusy(RuntimeError):
    """Another email-monitor writer holds the state directory."""


def _lock_byte(stream, unlock=False):
    stream.seek(0)
    if sys.platform == 'win32':
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream, fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


def writer_holder(state_dir):
    """What the current holder recorded, or None. Reads past the locked byte, so it never blocks."""
    try:
        with open(Path(state_dir) / WRITER_LOCK, 'rb') as stream:
            stream.seek(1)
            holder = json.loads(stream.read().decode('utf-8') or 'null')
        return holder if isinstance(holder, dict) else None
    except (OSError, ValueError):
        return None


@contextmanager
def writer_lock(state_dir, role):
    """Hold the state directory's writer lock, or raise WriterBusy at once (never waits)."""
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / WRITER_LOCK
    with open(path, 'a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        try:
            _lock_byte(stream)
        except OSError:
            holder = writer_holder(directory) or {}
            raise WriterBusy('another email-monitor writer (%s, pid %s, since %s) holds the state directory '
                             '%s; run again after it exits' % (
                                 holder.get('role', 'unknown'), holder.get('pid', '?'),
                                 holder.get('since', '?'), directory)) from None
        try:
            stream.truncate(1)
            stream.write(json.dumps({'role': role, 'pid': os.getpid(), 'since': datetime.datetime.now(
                datetime.timezone.utc).isoformat(timespec='seconds')}).encode('utf-8'))
            stream.flush()
            yield str(path)
        finally:
            _lock_byte(stream, unlock=True)


def atomic_write(destination, content):
    """Publish UTF-8 DATA through an exclusive temporary file in the destination's directory.

    The destination was checked once where it was chosen (storage_config or a CLI entry point).
    """
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def resolve_path(value, companion):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('path must be a nonempty string')
    path = Path(value).expanduser()
    return str((path if path.is_absolute() else Path(companion) / path).absolute())


def reject_model_overrides(**controls):
    """Legacy call arguments may be omitted, but cannot select model policy."""
    obsolete = [name for name, value in controls.items() if value is not None]
    if obsolete:
        raise ValueError('Remove obsolete model controls: ' + ', '.join(obsolete) +
                         '; configure routing, model, timeout and fallback in installed llmcall')


def check_model_settings(cfg):
    """Reject stale registry settings before credentials, mail or dispatch."""
    policy_keys = {'chain', 'providers', 'timeout', 'timeout_sec', 'model', 'reasoning_effort',
                   'codex_model', 'codex_reasoning', 'claude_model', 'fallback'}
    for section in ('classifier', 'topic_labeling', 'quality_review'):
        settings = cfg.get(section) or {}
        if not isinstance(settings, dict):
            raise ValueError(section + ' must be an object')
        obsolete = sorted(policy_keys.intersection(settings))
        if obsolete:
            raise ValueError('Remove obsolete ' + section + ' settings: ' + ', '.join(obsolete) +
                             '; configure routing, model, timeout and fallback in installed llmcall')


def runtime_config(cfg, companion):
    check_model_settings(cfg)
    runtime = cfg.get('runtime', {})
    if not isinstance(runtime, dict):
        raise ValueError('runtime must be an object')
    if not isinstance(runtime.get('local_only', False), bool):
        raise ValueError('runtime.local_only must be a boolean')
    return {**runtime, 'python': resolve_path(runtime.get('python', sys.executable), companion)}


def check_local_route(runtime, classifier, topic_enabled):
    if runtime.get('local_only', False) and (
            classifier.get('mode', 'agent') == 'agent' or topic_enabled):
        raise ValueError('local_only model capability unavailable: installed llmcall interface '
                         'does not provide enforceable local transport proof; use heuristic '
                         'classification with topic_labeling disabled')


def storage_config(cfg, companion, state_dir=None, db=None, log=None):
    storage = cfg.get('storage', {})
    if not isinstance(storage, dict):
        raise ValueError('storage must be an object')
    values = {'state_dir': state_dir or storage.get('state_dir', 'data/state'),
              'db': db or storage.get('db', 'data/pool.db'),
              'log': log or storage.get('log', 'data/email-monitor.log')}
    resolved = {key: resolve_path(value, companion) for key, value in values.items()}
    for path in resolved.values():
        private_path(path)
    return resolved


def probe_interpreter(python, timeout=10):
    """Run the selected executable, checking imports and a structured probe result."""
    if not Path(python).is_file():
        return False, 'selected interpreter file is missing'
    if os.path.normcase(os.path.abspath(python)) == os.path.normcase(os.path.abspath(sys.executable)):
        # The selected interpreter is this one: import in process instead of starting another.
        try:
            import imaplib  # noqa: F401
            import llmcall
        except ImportError:
            return False, 'selected interpreter or required llmcall dependency is unavailable'
        ok = callable(getattr(llmcall, 'call', None))
        return ok, 'interpreter and llmcall import verified' if ok else 'runtime probe failed'
    code = ('import json,imaplib,llmcall; '
            'assert callable(llmcall.call); '
            'print(json.dumps({"email_monitor_runtime":1,"llmcall":True}))')
    env = {k: v for k, v in os.environ.items() if k != 'GMAIL_APP_PW'}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    try:
        result = subprocess.run([python, '-B', '-c', code], capture_output=True, text=True,
                                encoding='utf-8', timeout=timeout, env=env,
                                **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
        payload = json.loads((result.stdout or '').strip())
        ok = result.returncode == 0 and payload == {'email_monitor_runtime': 1, 'llmcall': True}
        return ok, 'interpreter and llmcall import verified' if ok else 'runtime probe failed'
    except (OSError, ValueError, subprocess.SubprocessError):
        return False, 'selected interpreter or required llmcall dependency is unavailable'
