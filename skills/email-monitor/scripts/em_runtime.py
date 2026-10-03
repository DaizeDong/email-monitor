"""Runtime configuration, PRIVATE DATA proofs and atomic publication."""
from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import subprocess
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[3]


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


def prove_private(destination):
    """Require an unaliased, history-backed, unignored PRIVATE companion path.

    Guards attests every physical and effective publication route using the local
    fresh visibility receipt. No remote command or visibility refresh is executed.
    This read-only snapshot must be repeated immediately before a later write.
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
    boundary = _storage_api()
    try:
        proof = boundary.prove_private_companion(root)
        if Path(proof.root) != root or not path.is_relative_to(root):
            raise ValueError('Git did not establish the nearest DATA worktree')
        boundary.read_private_companion_git(proof, 'rev-parse', '--verify', 'HEAD')
        relative = path.relative_to(root).as_posix()
        ignored = boundary.read_private_companion_git(
            proof, 'check-ignore', '--no-index', '-q', '--', relative)
        if ignored.returncode == 0:
            raise ValueError('DATA must remain eligible for private version history')
    except (boundary.GitError, OSError) as exc:
        raise ValueError('DATA destination is not in a verifiable PRIVATE Git companion: ' + str(exc)) from exc
    _plain_path(path)
    return {'path': str(path), 'root': proof.root, 'repositories': list(proof.repositories),
            'repository': ', '.join(proof.repositories), 'visibility': 'PRIVATE',
            'proof': 'local-receipt', 'signature': proof.signature}


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
