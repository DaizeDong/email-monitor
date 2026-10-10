"""Pure durable-action identities, receipt validation and loaded-state validation."""
import copy
import hashlib
import json

ACTIONS = {'alert', 'pool', 'archive', 'topic_label'}
STATES = {'pending', 'failed', 'uncertain', 'completed', 'message_gone'}
# A mail action whose message no longer exists anywhere in the mailbox: after GONE_AFTER
# consecutive not_applied receipts that matched no message, the row stops being retried and no
# longer counts as pending work. Only mail effects can end this way.
GONE_ACTIONS = {'topic_label', 'archive'}
GONE_AFTER = 3


def matched_nothing(receipt):
    """A not_applied receipt whose search matched no message at all (structured, not by wording)."""
    return (isinstance(receipt, dict) and receipt.get('status') == 'not_applied'
            and type(receipt.get('matched')) is int and receipt['matched'] == 0)


def record_outcome(row, receipt, disposition):
    """Apply a dispatch outcome to `row`, counting consecutive "matched no message" answers.

    Returns the row's new status. confirmed -> completed; not_applied -> failed, or message_gone
    once GONE_AFTER consecutive not_applied receipts of a mail action matched no message;
    anything else leaves the status as it is (uncertain).
    """
    if disposition == 'confirmed':
        row.pop('gone_checks', None)
        row.update(status='completed', receipt=copy.deepcopy(receipt))
    elif disposition == 'not_applied':
        if row['action'] in GONE_ACTIONS and matched_nothing(receipt):
            row['gone_checks'] = int(row.get('gone_checks') or 0) + 1
        else:
            row.pop('gone_checks', None)
        gone = row['action'] in GONE_ACTIONS and row.get('gone_checks', 0) >= GONE_AFTER
        row.update(status='message_gone' if gone else 'failed', receipt=copy.deepcopy(receipt))
    else:
        row.pop('gone_checks', None)
    return row['status']


def open_work(row):
    """True while a row still owes work: neither completed nor proven message_gone."""
    return row['status'] not in ('completed', 'message_gone')


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
        if row['status'] == 'message_gone' and (
                row['action'] not in GONE_ACTIONS or not matched_nothing(row['receipt'])
                or receipt_status(row['receipt'], key, row['action']) != 'not_applied'
                or int(row.get('gone_checks') or 0) < GONE_AFTER):
            raise ValueError('message_gone action has no matching proof that the message is gone')
    return state
