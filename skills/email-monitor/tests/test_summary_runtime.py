"""Selected interpreter reaches summary adapters; inputs come from generated cases."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import em_summary as summary
from test_standalone_cli_boundary import FIX, private_cli


@pytest.mark.parametrize('override', [False, True])
def test_summary_uses_selected_python_for_every_adapter(private_cli, monkeypatch, override):
    configured = str(private_cli.root/'configured-python')
    selected = sys.executable if override else configured
    private_cli.registry['runtime']['python'] = configured
    (private_cli.root/'registry.json').write_text(json.dumps(private_cli.registry))
    calls = []
    item_id = FIX['message']['message_id']
    def pool(reminder, db, verb, args=None, python=None):
        calls.append((verb, python))
        if verb == 'due':
            return {'items': [{'id': item_id, 'ext': {'x_email_monitor_kind': 'daily-summary'}}]}
        if verb == 'list':
            return {'items': [{'title': FIX['message']['subject'], 'priority': 1, 'state': 'pending'}]}
        if verb == 'done':
            return {'item': {'id': item_id, 'state': 'done'}}
        assert verb == 'add'
        return {'item': {'id': item_id, 'idempotency_key': args[args.index('--idempotency-key')+1],
                         'due_at': args[args.index('--due-at')+1]}}
    def alert(message, idempotency_key=None, python=None):
        calls.append(('alert', python))
        return {'status': 'confirmed', 'adapter': 'alert', 'idempotency_key': idempotency_key,
                'receipt_id': item_id}
    monkeypatch.setattr(summary.em_pool, '_run', pool)
    monkeypatch.setattr(summary.em_alert, 'send', alert)
    argv = ['em_summary.py', '--config', str(private_cli.root/'registry.json')]
    if override:
        argv += ['--python', selected]
    monkeypatch.setattr(sys, 'argv', argv)
    assert summary.main() == 0
    assert [verb for verb, _ in calls] == ['due', 'list', 'alert', 'done', 'add']
    assert all(python == selected for _, python in calls)


def test_summary_rejects_unversioned_registry_before_read(tmp_path, monkeypatch):
    registry = tmp_path/'registry.json'
    registry.write_text('{}')
    monkeypatch.setattr(summary.json, 'load', lambda *a, **k: pytest.fail('private registry read'))
    monkeypatch.setattr(sys, 'argv', ['em_summary.py', '--config', str(registry)])
    assert summary.main() == 1
