"""Runtime configuration, PRIVATE DATA proofs, the state writer lock and atomic publication."""
from contextlib import contextmanager
import datetime
from functools import lru_cache
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import subprocess
import sys
import threading
import time

SOURCE_ROOT = Path(__file__).resolve().parents[3]


def _resolved_companion_root():
    """Use the pinned Guards discovery convention for the private repository root."""
    source = SOURCE_ROOT / 'guards/tools/datadir.py'
    if not source.is_file():
        raise ValueError('Initialize the pinned Guards submodule before discovering configuration')
    spec = importlib.util.spec_from_file_location('email_monitor_companion_resolver', source)
    resolver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(resolver)
    return resolver.resolve_companion_root('email-monitor')


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


@lru_cache(maxsize=1)
def _storage_api():
    """Load the supported API from the required Guards submodule."""
    source = SOURCE_ROOT / 'guards/tools/data_boundary.py'
    if not source.is_file():
        raise ValueError('Initialize the pinned Guards submodule before using PRIVATE storage')
    spec = importlib.util.spec_from_file_location('email_monitor_storage_boundary', source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _contract_api():
    source = SOURCE_ROOT / 'guards/tools/storage_contract.py'
    if not source.is_file():
        raise ValueError('Initialize the pinned Guards submodule before configuration writes')
    spec = importlib.util.spec_from_file_location('email_monitor_storage_contract', source)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def authorize_config_write(root, destination):
    root = Path(root).expanduser().absolute()
    path = _plain_path(destination)
    return _contract_api().authorize_artifact_write(
        SOURCE_ROOT, root, path.relative_to(root).as_posix())


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


# A full PRIVATE proof runs dozens of Git queries (3.7 to 4.9 s each on the live companion), and
# atomic_write proves five times per save, so a tick that saves after every row spent about 20 s
# per save on proofs alone (about one row a minute while draining a backlog). A successful proof
# is therefore reused within this process while everything it depends on is unchanged, the same
# rule as schedule-reminder's private_data memo: the companion root and its Git administration,
# the companion's own Git configuration (remote URLs), HEAD and the ref it names, info/exclude,
# the global and system Git configuration (including the files GIT_CONFIG_GLOBAL /
# GIT_CONFIG_SYSTEM name and literal core.excludesFile targets), the SSH client configuration the
# proof attests, the local visibility receipt, the process environment and the proof
# implementation. Any change is a miss and proves in full. A refusal is never stored, so it is
# proved again next time. Entries expire after PROOF_TTL_SECONDS. Bounded only by that TTL:
# configuration reached only through include directives, the receipt ageing past its maximum age
# and changes inside the Git installation itself. Ignore status is checked per destination with
# the proof's own read-only Git query, and again whenever a .gitignore that can decide it changes.
# The memo is not used under GIT_CEILING_DIRECTORIES. The path checks (_plain_path, nested and
# bare boundaries) run on every call.
PROOF_TTL_SECONDS = 60.0
_PROOF_LOCK = threading.RLock()
_PROOF_MEMO = {}
_RELATIVES_CAP = 256
_UNREADABLE = object()


def clear_proof_memo():
    """Forget every reused PRIVATE proof in this process."""
    with _PROOF_LOCK:
        _PROOF_MEMO.clear()


def _clock():
    return time.monotonic()


def _file_state(path, digest=True):
    try:
        if digest:
            with open(path, 'rb') as stream:
                return hashlib.sha256(stream.read()).hexdigest()
        info = os.stat(path)
        return (info.st_mtime_ns, info.st_size)
    except FileNotFoundError:
        return None
    except OSError:
        return _UNREADABLE


def _admin_dirs(root):
    marker = root / '.git'
    if marker.is_dir():
        admin = marker
    elif marker.is_file():
        text = marker.read_text(encoding='utf-8').strip()
        if not text.startswith('gitdir:'):
            return None
        admin = Path(text[7:].strip())
        admin = (admin if admin.is_absolute() else root / admin).resolve()
    else:
        return None
    common = admin
    if (admin / 'commondir').is_file():
        common = (admin / (admin / 'commondir').read_text(encoding='utf-8').strip()).resolve()
    return admin, common


def _excludes_targets(path, home):
    """Literal core.excludesFile values in one config file (include chains are TTL-bounded)."""
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return []
    targets = []
    for line in text.splitlines():
        name, separator, value = line.strip().partition('=')
        if separator and name.strip().casefold() == 'excludesfile':
            value = value.strip().strip('"')
            if value.startswith('~/') or value == '~':
                value = str(home) + value[1:]
            if value:
                targets.append(Path(value))
    return targets


def _git_system_files(home):
    """System Git and SSH configuration files the proof may read; a superset is fine."""
    import shutil
    paths = set()
    for name in ('GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM'):
        if os.environ.get(name):
            paths.add(Path(os.environ[name]))
    profiles = {str(home), os.environ.get('HOME'), os.environ.get('USERPROFILE')}
    paths.update(Path(profile) / '.ssh' / 'config' for profile in profiles if profile)
    if os.name == 'nt':
        program_data = os.environ.get('ProgramData')
        if program_data:
            paths.add(Path(program_data) / 'ssh' / 'ssh_config')
            paths.add(Path(program_data) / 'Git' / 'config')
        git = shutil.which('git')
        if git:
            for installation in list(Path(git).resolve().parents)[:4]:
                paths.update({installation / 'etc' / 'gitconfig', installation / 'etc' / 'ssh' / 'ssh_config',
                              installation / 'mingw64' / 'etc' / 'gitconfig'})
    else:
        paths.update({Path('/etc/gitconfig'), Path('/etc/ssh/ssh_config')})
    return paths


def _companion_state(root):
    """Cheap local fingerprint of what a proof depends on; None when it cannot be read."""
    try:
        dirs = _admin_dirs(root)
        if dirs is None:
            return None
        admin, common = dirs
        parts = [str(root), str(admin), str(common),
                 _file_state(SOURCE_ROOT / 'guards/tools/data_boundary.py'),
                 hashlib.sha256(json.dumps(sorted(os.environ.items())).encode('utf-8')).hexdigest(),
                 _file_state(admin / 'HEAD')]
        try:
            text = (admin / 'HEAD').read_text(encoding='utf-8').strip()
        except FileNotFoundError:
            text = ''
        if text.startswith('ref:'):
            ref = text[4:].strip()
            if '..' in ref.split('/'):
                return None
            parts += [_file_state(admin / ref), _file_state(common / ref)]
        parts += [_file_state(admin / 'config'), _file_state(common / 'config'),
                  _file_state(admin / 'config.worktree'), _file_state(common / 'info' / 'exclude'),
                  _file_state(common / 'packed-refs', digest=False)]
        home = Path(os.path.expanduser('~'))
        xdg = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
        profiles = {home, Path(os.environ.get('HOME') or home), Path(os.environ.get('USERPROFILE') or home)}
        files = {xdg / 'git' / 'config', xdg / 'git' / 'ignore'}
        for profile in profiles:
            files |= {profile / '.gitconfig', profile / '.pii-guard' / 'visibility.json'}
        files |= _git_system_files(home)
        # SSH files are only hashed here, never parsed: an HTTPS route must not interpret them.
        for config in sorted(files | {admin / 'config', common / 'config', admin / 'config.worktree'}, key=str):
            if config.name != 'ssh_config' and config.parent.name != '.ssh':
                files.update(_excludes_targets(config, home))
        for path in sorted(files, key=str):
            parts.append((str(path), _file_state(path)))
    except (OSError, ValueError):
        return None
    if any(part is _UNREADABLE or isinstance(part, tuple) and part[-1] is _UNREADABLE for part in parts):
        return None
    return hashlib.sha256(repr(parts).encode('utf-8')).hexdigest()


def _ignore_state(root, relative):
    """Fingerprint the .gitignore files that can decide check-ignore for ``relative``."""
    directory, states = root, [_file_state(root / '.gitignore')]
    for part in relative.rstrip('/').split('/')[:-1]:
        if part in ('', '.'):
            continue
        directory = directory / part
        states.append(_file_state(directory / '.gitignore'))
    if any(state is _UNREADABLE for state in states):
        return None
    return hashlib.sha256(repr(states).encode('utf-8')).hexdigest()


def _implementation(boundary):
    return (boundary.prove_private_companion, boundary.read_private_companion_git,
            getattr(boundary, '_ssh_config_sources', None))


def _proof_result(path, proof):
    return {'path': str(path), 'root': proof.root, 'repositories': list(proof.repositories),
            'repository': ', '.join(proof.repositories), 'visibility': 'PRIVATE',
            'proof': 'local-receipt', 'signature': proof.signature}


def _memo_lookup(path, root):
    """A result from a still-valid memo entry, or None to prove in full."""
    if os.environ.get('GIT_CEILING_DIRECTORIES'):
        return None
    key = os.path.normcase(str(root))
    entry = _PROOF_MEMO.get(key)
    if entry is None:
        return None
    boundary = _storage_api()
    if (_clock() - entry['proved_at'] > PROOF_TTL_SECONDS
            or any(a is not b for a, b in zip(entry['implementation'], _implementation(boundary)))
            or _companion_state(root) != entry['state']):
        _PROOF_MEMO.pop(key, None)
        return None
    relative = path.relative_to(root).as_posix()
    if relative != '.':
        ignore = _ignore_state(root, relative)
        if ignore is None:
            return None
        if entry['relatives'].get(relative) != ignore:
            try:
                ignored = boundary.read_private_companion_git(
                    entry['proof'], 'check-ignore', '--no-index', '-q', '--', relative)
            except (boundary.GitError, OSError, ValueError):
                _PROOF_MEMO.pop(key, None)
                return None
            if ignored.returncode == 0:
                raise ValueError('DATA must remain eligible for private version history')
            if ignored.returncode != 1 or _ignore_state(root, relative) != ignore:
                return None
            if len(entry['relatives']) >= _RELATIVES_CAP:
                entry['relatives'].clear()
            entry['relatives'][relative] = ignore
    return _proof_result(path, entry['proof'])


def prove_private(destination):
    """Require an unaliased, history-backed, unignored PRIVATE companion path.

    Guards attests every physical and effective publication route using the local
    fresh visibility receipt. No remote command or visibility refresh is executed.
    This read-only snapshot must be repeated immediately before a later write. A
    successful proof is reused within this process for an unchanged companion (see
    PROOF_TTL_SECONDS), so a repeat is a fingerprint check; a refusal is never reused.
    """
    path = _plain_path(destination)
    containing = path if path.is_dir() else path.parent
    root = None
    for directory in (containing, *containing.parents):
        if os.path.lexists(directory / '.git'):
            if root is not None:
                raise ValueError('DATA companion cannot be nested in another repository')
            root = directory
        elif (directory / 'HEAD').is_file() and (directory / 'objects').is_dir():
            raise ValueError('DATA cannot fall through a bare repository boundary')
    if root is None:
        raise ValueError('DATA destination requires a PRIVATE Git companion')
    if root.is_relative_to(SOURCE_ROOT) or SOURCE_ROOT.is_relative_to(root):
        raise ValueError('DATA requires a separate PRIVATE worktree')
    with _PROOF_LOCK:
        reused = _memo_lookup(path, root)
        if reused is not None:
            _plain_path(path)
            return reused
        return _prove_and_remember(path, root)


def _prove_and_remember(path, root):
    started = _clock()  # an entry's age counts from before the proof, never after it
    state_before = _companion_state(root)
    boundary = _storage_api()
    implementation = _implementation(boundary)
    try:
        proof = boundary.prove_private_companion(root)
        if Path(proof.root) != root or not path.is_relative_to(root):
            raise ValueError('Git did not establish the nearest DATA worktree')
        boundary.read_private_companion_git(proof, 'rev-parse', '--verify', 'HEAD')
        relative = path.relative_to(root).as_posix()
        ignore_before = _ignore_state(root, relative) if relative != '.' else None
        # The root is a container, not a relative DATA entry. Git's dot path
        # can match a blank ignore rule; verify each DATA destination separately.
        if relative != '.':
            ignored = boundary.read_private_companion_git(
                proof, 'check-ignore', '--no-index', '-q', '--', relative)
            if ignored.returncode == 0:
                raise ValueError('DATA must remain eligible for private version history')
            if ignored.returncode != 1:
                raise ValueError('Git could not establish DATA version eligibility')
    except (boundary.GitError, OSError) as exc:
        raise ValueError('DATA destination is not in a verifiable PRIVATE Git companion: ' + str(exc)) from exc
    _plain_path(path)
    # Remember only what this proof established, and only if nothing it depends on moved while
    # it ran. The result is returned either way.
    if (not os.environ.get('GIT_CEILING_DIRECTORIES') and state_before is not None
            and _companion_state(root) == state_before):
        entry = {'proof': proof, 'implementation': implementation, 'proved_at': started,
                 'state': state_before, 'relatives': {}}
        if relative != '.' and ignore_before is not None and _ignore_state(root, relative) == ignore_before:
            entry['relatives'][relative] = ignore_before
        _PROOF_MEMO[os.path.normcase(str(root))] = entry
    return _proof_result(path, proof)


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
    """Publish UTF-8 DATA through an exclusive temporary file in the same companion."""
    before = prove_private(destination)
    path = Path(before['path'])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None

    def check(current):
        if any(current[key] != before[key] for key in ('root', 'repositories', 'signature')):
            raise ValueError('PRIVATE companion changed during atomic write')

    try:
        check(prove_private(path))
        descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', suffix='.tmp', dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as handle:
            identity = os.fstat(handle.fileno())
            check(prove_private(temporary))
            if not os.path.samestat(identity, temporary.stat()):
                raise ValueError('Atomic temporary file changed before write')
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        check(prove_private(path))
        check(prove_private(temporary))
        if not os.path.samestat(identity, temporary.stat()):
            raise ValueError('Atomic temporary file changed before publication')
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


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
    proofs = {key: prove_private(path) for key, path in resolved.items()}
    return resolved, proofs


def probe_interpreter(python, timeout=10):
    """Run the selected executable, checking imports and a structured probe result."""
    if not Path(python).is_file():
        return False, 'selected interpreter file is missing'
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
