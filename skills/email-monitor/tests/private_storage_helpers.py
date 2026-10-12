"""Materialize generator-owned repositories for native offline storage tests."""
import importlib.util
from pathlib import Path
import shutil

_spec = importlib.util.spec_from_file_location(
    'email_private_fixtures', Path(__file__).resolve().parents[3] / 'tools/make_fixtures.py')
generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generator)


def make_repository(root):
    root = Path(root)
    for name, content in generator.private_repository_fixture().items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root


def make_linked_worktree(root, common):
    """Give native Git the same administration layout as git worktree add."""
    root, common = Path(root), Path(common)
    shutil.move(str(root / '.git'), str(common))
    metadata = common / 'worktrees/synthetic'
    metadata.mkdir(parents=True)
    (metadata / 'commondir').write_text('../..\n', encoding='utf-8')
    (metadata / 'gitdir').write_text(str(root / '.git') + '\n', encoding='utf-8')
    (metadata / 'HEAD').write_bytes((common / 'HEAD').read_bytes())
    (root / '.git').write_text('gitdir: ' + str(metadata) + '\n', encoding='utf-8')
