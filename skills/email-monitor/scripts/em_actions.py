"""Pure durable-action identities, receipt validation and loaded-state validation."""
import copy
import hashlib
import json

ACTIONS = {'alert', 'pool', 'archive', 'topic_label'}
STATES = {'pending', 'failed', 'uncertain', 'completed'}


def identity(account, mailbox, uidvalidity, message_id, action='', label=''):
    scope = [account.strip().lower(), mailbox, str(uidvalidity), message_id.strip(), action, label]
    return hashlib.sha256(json.dumps(scope, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def receipt_status(receipt, key, adapter):
    if not isinstance(receipt, dict) or receipt.get('idempotency_key') != key or receipt.get('adapter') != adapter:
        return 'uncertain'
    kind = receipt.get('status')
    required = 'receipt_id' if kind == 'confirmed' else 'evidence' if kind == 'not_applied' else None
    if required and isinstance(receipt.get(required), str) and receipt[required].strip():
        return kind
    return 'uncertain'


def new_action(account, mailbox, generation, record, action, payload):
    mid = record.get('message_id', '').strip()
    if not mid or generation is None:
        raise ValueError('message identity and UIDVALIDITY are required for durable actions')
    key = identity(account, mailbox, generation, mid, action,
                   payload.get('label', '') if action == 'topic_label' else '')
    return {'idempotency_key': key, 'account': account.strip().lower(), 'mailbox': mailbox,
            'uidvalidity': generation, 'message_id': mid, 'action': action,
            'status': 'pending', 'payload': payload, 'receipt': None}


def load_state(raw, account):
    """Copy before planning. Never reinterpret damaged or unknown work as empty."""
    if not isinstance(raw, dict):
        raise ValueError('state must be an object')
    state = copy.deepcopy(raw)
    for key, default in [('cursors', {}), ('actions', {}), ('observed_messages', []), ('topic_retry', [])]:
        state.setdefault(key, copy.deepcopy(default))
        if not isinstance(state[key], type(default)):
            raise ValueError('malformed state field: ' + key)
    for key, value in state.items():
        if key.startswith('pending') and value:
            raise ValueError('legacy pending queue needs explicit migration: ' + key)
    for entry in state['topic_retry']:
        if not isinstance(entry, dict) or not isinstance(entry.get('message_id'), str) or not entry['message_id'].strip():
            raise ValueError('malformed legacy topic_retry; preserve and repair it before resuming')
        entry.setdefault('mailbox', 'INBOX')
        if entry.get('uidvalidity') is None:
            cursor = state['cursors'].get(account.strip().lower() + '::' + entry['mailbox'], {})
            entry['uidvalidity'] = cursor.get('uidvalidity')
        if entry['uidvalidity'] is None:
            raise ValueError('legacy topic retry has no provable mailbox generation; migration required')
    for key, row in state['actions'].items():
        required = {'idempotency_key', 'account', 'mailbox', 'uidvalidity', 'message_id',
                    'action', 'status', 'payload', 'receipt'}
        if not isinstance(row, dict) or not required.issubset(row):
            raise ValueError('malformed durable action')
        if not isinstance(key, str) or not key or key != row['idempotency_key']:
            raise ValueError('invalid durable action key')
        if row['account'] != account.strip().lower() or row['action'] not in ACTIONS or row['status'] not in STATES:
            raise ValueError('invalid durable action scope or status')
        if not row['mailbox'] or row['uidvalidity'] is None or not row['message_id'] or not isinstance(row['payload'], dict):
            raise ValueError('invalid durable action identity or payload')
        if row['status'] == 'completed' and receipt_status(row['receipt'], key, row['action']) != 'confirmed':
            raise ValueError('completed action has no matching acknowledgement')
    return state
