"""Read-only runtime configuration and PRIVATE DATA boundary checks."""
from datetime import datetime, timezone
import fnmatch
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
from subprocess import run as _run
import sys
from urllib.parse import urlsplit

SOURCE_ROOT = Path(__file__).resolve().parents[3]
MAX_CACHE_AGE_DAYS = 30


def valid_account_slug(slug):
    """Accept one portable account state filename component."""
    return (isinstance(slug, str) and re.fullmatch(r"[A-Za-z0-9_.@+-]+", slug) is not None
            and slug not in (".", "..")
            and re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", slug, re.I) is None)


def _query(arguments):
    result = _run(arguments, capture_output=True, text=True, encoding='utf-8', timeout=20)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, arguments)
    return result.stdout.strip()


def _ssh_hostname(alias):
    """Interpret ordinary Host/HostName rules without evaluating SSH commands."""
    active, hostname = True, None
    for line in (Path.home()/'.ssh/config').read_text(encoding='utf-8').splitlines():
        fields = shlex.split(re.sub(r'^(\s*\w+)\s*=\s*', r'\1 ', line), comments=True)
        if not fields:
            continue
        key, values = fields[0].lower(), fields[1:]
        if key in {'include', 'match', 'canonicaldomains'} or key.startswith('canonicalize'):
            raise ValueError('PRIVATE proof supports ordinary SSH Host/HostName rules only')
        if key == 'host':
            if not values:
                raise ValueError('SSH Host requires a pattern')
            patterns = [value.lower() for value in values]
            active = (any(fnmatch.fnmatchcase(alias, p) for p in patterns if not p.startswith('!'))
                      and not any(fnmatch.fnmatchcase(alias, p[1:]) for p in patterns if p.startswith('!')))
        elif key == 'hostname':
            if len(values) != 1:
                raise ValueError('SSH HostName requires one hostname')
            if active and hostname is None:
                hostname = values[0].lower()
    return hostname


def _repository_identity(remote):
    if not isinstance(remote, str) or not remote or any(c.isspace() for c in remote):
        raise ValueError('DATA origin does not identify a GitHub repository')
    ssh = True
    if '://' in remote:
        parsed = urlsplit(remote)
        if (parsed.scheme not in {'https', 'ssh'} or parsed.password or parsed.query or parsed.fragment
                or parsed.port is not None or parsed.username not in (None, 'git')):
            raise ValueError('unsupported DATA origin')
        host, name = parsed.hostname, parsed.path.removeprefix('/')
        ssh = parsed.scheme == 'ssh'
        if not ssh and parsed.username is not None:
            raise ValueError('HTTPS origin cannot contain credentials')
    else:
        parsed = re.fullmatch(r'(?:git@)?([^/:\s]+):([^\s]+)', remote)
        if parsed is None:
            raise ValueError('DATA origin visibility is unknown')
        host, name = parsed.groups()
    host = host.lower() if host else None
    if ssh and host and host != 'github.com':
        host = _ssh_hostname(host)
    name = name.removesuffix('.git')
    if (host != 'github.com' or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*', name)
            or any(part.endswith('.') for part in name.split('/'))):
        raise ValueError('DATA origin must identify a GitHub repository')
    return name.lower()


def _cached_private(name):
    """A failed live lookup may use only one recent, timezone-aware PRIVATE proof."""
    cache = Path.home()/'.pii-guard/visibility.json'
    try:
        visibility = json.loads(cache.read_text(encoding='utf-8-sig'))
        stamp = visibility['_refreshed']
        refreshed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if refreshed.tzinfo is None:
            raise ValueError('cache timestamp has no timezone')
        age = (datetime.now(timezone.utc)-refreshed).total_seconds()
        entries = [v for k,v in visibility.items() if k.casefold() == name.casefold()]
        if not 0 <= age <= MAX_CACHE_AGE_DAYS*86400 or len(entries) != 1:
            raise ValueError('cache is stale, future-dated or ambiguous')
        value = entries[0]
        if isinstance(value, dict):
            value = value.get('v', value.get('visibility'))
        if value != 'PRIVATE':
            raise ValueError('cache does not prove PRIVATE')
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError('Current PRIVATE visibility unavailable; authenticate gh or refresh the visibility cache') from exc


def _prove_visibility(name):
    try:
        output = _query(['gh', 'repo', 'view', name, '--json', 'nameWithOwner,visibility'])
    except (OSError, subprocess.SubprocessError):
        _cached_private(name)
        return 'recent-cache'
    answer = json.loads(output)
    if (not isinstance(answer, dict) or answer.get('visibility') != 'PRIVATE'
            or str(answer.get('nameWithOwner', name)).casefold() != name.casefold()):
        raise ValueError('DATA origin must have current verified PRIVATE visibility')
    return 'live'


def prove_private(destination):
    """Resolve an output's nearest Git repository, including linked worktrees.

    Git establishes the governing repository; current GitHub visibility proves
    privacy. A bounded recent cache is usable only when the live lookup fails.
    This function creates neither DATA directories nor cache files.
    """
    path = Path(destination).expanduser().resolve()
    if path.is_relative_to(SOURCE_ROOT) or SOURCE_ROOT.is_relative_to(path):
        raise ValueError('DATA destination is inside the public source tree')
    if any(part.lower() == '.git' for part in path.parts):
        raise ValueError('DATA cannot be written into Git metadata')
    existing = path
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if existing.is_file():
        existing = existing.parent
    try:
        discovered = _query(['git', '-C', str(existing), 'rev-parse', '--show-toplevel'])
        root = Path(discovered)
        if not discovered or not root.is_absolute():
            raise ValueError('Git did not establish a worktree')
        root = root.resolve()
        if not root.is_dir() or not path.is_relative_to(root) or SOURCE_ROOT.is_relative_to(root):
            raise ValueError('DATA requires a separate PRIVATE worktree')
        name = _repository_identity(_query(['git', '-C', str(root), 'remote', 'get-url', 'origin']))
        proof = _prove_visibility(name)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('DATA destination is not in a verifiable PRIVATE Git companion') from exc
    return {'path': str(path), 'repository': name, 'visibility': 'PRIVATE', 'proof': proof}


def resolve_path(value, companion):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('path must be a nonempty string')
    path = Path(value).expanduser()
    return str((path if path.is_absolute() else Path(companion) / path).resolve())


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
