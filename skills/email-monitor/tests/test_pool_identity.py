import copy
import importlib.util
from pathlib import Path
import sys
import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import em_pool
spec=importlib.util.spec_from_file_location('identity_fixtures',SCRIPTS.parents[2]/'tools/make_fixtures.py')
fixtures=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


@pytest.mark.parametrize('actionable', [False, True])
@pytest.mark.parametrize('fail_update_once', [False, True])
def test_archived_information_can_promote_to_new_action(monkeypatch,tmp_path,actionable,fail_update_once):
    import json
    row=fixtures.archived_notification()
    fail_next=fail_update_once
    def run(reminder,db,verb,args,**kwargs):
        nonlocal fail_next
        if verb=='list': return {'items':[copy.deepcopy(row)]}
        if verb=='transition': row['state']=args[args.index('--to')+1]
        elif verb=='update':
            if fail_next:
                fail_next=False
                raise em_pool.PoolError('ERR_BUSY','example interrupted update')
            row['ext'].update(json.loads(args[args.index('--ext')+1]))
            for n,arg in enumerate(args):
                if arg=='--set':
                    key,value=args[n+1].split('=',1);row[key]=value
        else: raise AssertionError(verb)
        return {'item':copy.deepcopy(row)}
    monkeypatch.setattr(em_pool,'_run',run)
    def write():return em_pool.upsert('example-cli',str(tmp_path/'db'),'<new@example.com>','thread-a',
                         'New action' if actionable else 'New information',
                         kind='task' if actionable else 'event',ext_extra={'account':'user1'})
    if fail_update_once:
        with pytest.raises(em_pool.PoolError):write()
    result=write()
    assert result['item']['state']==('pending' if actionable else 'cancelled')
    assert result['item']['kind']==('task' if actionable else 'event')
    if actionable:
        assert result['item']['title']=='New action'
        assert result['item']['ext']['x_email_monitor_notification_archive'] is None

def test_reviewed_rule_requires_account_sender_entity_and_expiry():
    item=fixtures.pool_merge_case()
    ext={'x_email_monitor_account':'user1','x_email_monitor_from':'Billing <billing@example.com>',
         'x_email_monitor_subject_raw':'Payment Reminder'}
    assert em_pool._matches_reviewed(item,ext,'invoice A-41 is overdue')
    assert not em_pool._matches_reviewed(item,ext,'invoice A-42 is overdue')
    assert not em_pool._matches_reviewed(item,{**ext,'x_email_monitor_account':'user2'},'invoice A-41')
    assert not em_pool._matches_reviewed(item,{**ext,'x_email_monitor_from':'other@example.com'},'invoice A-41')
    item['ext']['x_email_monitor_merge_rules'][0]['until']='2000-01-01'
    assert not em_pool._matches_reviewed(item,ext,'invoice A-41')
    item['ext']['x_email_monitor_merge_rules'][0]['contains']='invoice A-41'
    with pytest.raises(em_pool.PoolError,match='ERR_MERGE_RULE'):
        em_pool._matches_reviewed(item,ext,'invoice A-41')

def test_replay_does_not_increment_or_overwrite_and_new_message_advances(monkeypatch,tmp_path):
    original=fixtures.pool_merge_case(); rows=[copy.deepcopy(original)]; calls=[]
    def run(reminder,db,verb,args,**kwargs):
        calls.append(verb)
        if verb=='list':return {'items':copy.deepcopy(rows)}
        if verb=='update':
            import json
            rows[0]['ext'].update(json.loads(args[args.index('--ext')+1]))
            for n,arg in enumerate(args):
                if arg=='--set':
                    key,value=args[n+1].split('=',1);rows[0][key]=value
            return {'item':copy.deepcopy(rows[0])}
        raise AssertionError(verb)
    monkeypatch.setattr(em_pool,'_run',run)
    def up(mid,title):return em_pool.upsert('synthetic-cli',str(tmp_path/'db'),mid,'thread-a',title,
                                           ext_extra={'account':'user1'})
    assert up('<first@example.com>','stale retry')['action']=='replayed'
    assert calls==['list']
    assert up('<next@example.com>','Current action')['item']['title']=='Current action'
    assert rows[0]['ext']['x_email_monitor_msg_count']==2
    assert up('<first@example.com>','old replay')['action']=='replayed'
    assert rows[0]['title']=='Current action'
    rows[0]['state']='done'
    assert up('<third@example.com>','Do again')['item']['title']=='Current action'
    assert rows[0]['state']=='done'

def test_account_scope_and_duplicate_redirect(monkeypatch):
    row=fixtures.pool_merge_case()
    duplicate=copy.deepcopy(row);duplicate['id']='old';duplicate['ext']['x_console_consolidation']={'duplicate_of':'invoice-a'}
    row['ext']['x_email_monitor_thread_key']='new-thread'
    monkeypatch.setattr(em_pool,'_run',lambda *a,**k:{'items':[duplicate,row]})
    assert em_pool.find_thread('cli',None,'thread-a',account='user1')['id']=='invoice-a'
    assert em_pool.find_thread('cli',None,'thread-a',account='user2') is None


def test_failed_pool_write_retains_intent_and_dry_run_has_no_writes(monkeypatch,tmp_path):
    import em_tick
    account,message,verdict=fixtures.pool_tick_case()
    from private_storage_helpers import make_repository
    companion=tmp_path/'companion'
    make_repository(companion)
    saved=[]
    calls=[]
    monkeypatch.setattr(em_tick,'resolve_app_pw',lambda *a:'synthetic-auth')
    monkeypatch.setattr(em_tick.em_watch,'load_state',lambda *a:{'cursors':{},'seen_gm_msgids':[]})
    monkeypatch.setattr(em_tick.em_watch,'run_once',lambda *a,**kw:([message],{'last_uid':2,'uidvalidity':1}))
    monkeypatch.setattr(em_tick.em_watch,'save_state',lambda path,state:saved.append(copy.deepcopy(state)))
    monkeypatch.setattr(em_tick,'classify_records_parallel',lambda *a:[verdict])
    def failed(*a,**kw):
        calls.append(kw['idempotency_key'])
        raise em_pool.PoolError('ERR_BUSY','synthetic failure')
    monkeypatch.setattr(em_pool,'upsert',failed)
    def run(dry):
        return em_tick.process_account(account,{'discord_push_levels':[]},'cli',None,None,
            str(companion/'state'),dry,agent_cfg={'mode':'heuristic'},log_path=str(companion/'run.log'))
    result=run(False)
    assert result['status']=='incomplete' and len(calls)==1
    pending=list(saved[-1]['actions'].values())
    assert len(pending)==1 and pending[0]['action']=='pool' and pending[0]['status']=='uncertain'
    assert pending[0]['message_id']==message['message_id']
    assert pending[0]['payload']['ext_extra']['account']==account['slug']
    assert message['body'] in pending[0]['payload']['match_text']
    before=copy.deepcopy(saved)
    assert run(True)['status']=='planned'
    assert saved==before and len(calls)==1


@pytest.mark.skipif(not em_pool.available(), reason="schedule-reminder base not installed")
def test_concurrent_adapter_calls_and_reviewed_cross_thread_merge(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import json
    cli=em_pool.default_reminder_path()
    assert Path(cli).is_file(), 'Integration test requires the configured owner CLI'
    db=str(tmp_path/'pool.sqlite3')
    em_pool._run(cli,db,'init',[])
    case=fixtures.pool_merge_case()
    def up(n):return em_pool.upsert(cli,db,f'<message-{n}@example.com>','thread-a','Invoice A-41',
                                   ext_extra={'account':'user1'})
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies=list(pool.map(up,range(3)))
    assert len({r['item']['id'] for r in replies})==1
    row=em_pool._items(cli,db)[0]
    assert row['ext']['x_email_monitor_msg_count']==3
    em_pool._run(cli,db,'update',['--id',row['id'],'--ext',json.dumps({
        'x_email_monitor_merge_rules':case['ext']['x_email_monitor_merge_rules']})])
    result=em_pool.upsert(cli,db,'<reminder@example.com>','new-thread','Invoice A-41 overdue',
        ext_extra={'account':'user1','from':'billing@example.com','subject_raw':'Payment Reminder'},
        match_text='Invoice A-41 is overdue')
    assert result['item']['id']==row['id'] and len(em_pool._items(cli,db))==1


def test_owner_cli_reads_do_not_add_files_to_installed_source(tmp_path,monkeypatch):
    monkeypatch.delenv('PYTHONDONTWRITEBYTECODE',raising=False)
    script=fixtures.bytecode_probe(tmp_path)
    before={p.name for p in tmp_path.iterdir()}
    assert em_pool._run(str(script),None,'list',[])=={'ok':True}
    assert {p.name for p in tmp_path.iterdir()}==before


@pytest.mark.skipif(not em_pool.available(), reason="schedule-reminder base not installed")
def test_durable_pool_receipts_survive_later_messages_and_legacy_replay(tmp_path):
    cli=em_pool.default_reminder_path()
    assert Path(cli).is_file(), 'Pass --reminder-source for native reminder integration'
    db=str(tmp_path/'receipts.sqlite3')
    native=fixtures.native_pool_case()
    case=native['first']
    def write(message,key):
        return em_pool.upsert(cli,db,message,case['ext']['x_email_monitor_thread_key'],case['title'],
            ext_extra={'account':case['ext']['x_email_monitor_account']},idempotency_key=key)
    first=case['ext']['x_email_monitor_message_id']
    assert write(first,None)['action']=='created'
    receipt=write(first,native['first_key'])
    assert receipt['status']=='confirmed'
    assert write(native['next_message'],native['next_key'])['status']=='confirmed'
    assert write(first,native['first_key'])==receipt
    rows=em_pool._items(cli,db)
    assert len(rows)==1 and rows[0]['ext']['x_email_monitor_msg_count']==2


def test_reviewed_merge_rejects_empty_entity_constraint():
    case=fixtures.pool_merge_case()
    case['ext']['x_email_monitor_merge_rules'][0]['contains']=[]
    with pytest.raises(em_pool.PoolError,match='ERR_MERGE_RULE'):
        em_pool._matches_reviewed(case,{},'')


@pytest.mark.skipif(not em_pool.available(), reason="schedule-reminder base not installed")
def test_reviewed_cross_account_merge_retains_both_source_identities_and_priority(tmp_path):
    import json
    cli=em_pool.default_reminder_path()
    db=str(tmp_path/'cross-account.sqlite3')
    native=fixtures.native_pool_case()
    case=native['first']
    account=case['ext']['x_email_monitor_account']
    thread=case['ext']['x_email_monitor_thread_key']
    original=case['ext']['x_email_monitor_message_id']
    first=em_pool.upsert(cli,db,original,thread,case['title'],kind='event',priority=native['information_priority'],
                         ext_extra={'account':account})['item']
    rule=copy.deepcopy(case['ext']['x_email_monitor_merge_rules'][0])
    rule['account']=native['second_account']
    em_pool._run(cli,db,'update',['--id',first['id'],'--ext',json.dumps({'x_email_monitor_merge_rules':[rule]})])
    second=em_pool.upsert(cli,db,native['second_message'],native['second_thread'],case['title'],priority=native['action_priority'],
        ext_extra={'account':native['second_account'],'from':rule['sender'],'subject_raw':rule['subject']})['item']
    assert second['id']==first['id'] and second['priority']==native['action_priority'] and second['kind']=='task'
    replay=em_pool.upsert(cli,db,original,thread,case['title'],ext_extra={'account':account})
    assert replay['action']=='replayed' and replay['item']['id']==first['id']
    assert em_pool.find_thread(cli,db,thread,account=account)['id']==first['id']
    assert em_pool.find_thread(cli,db,native['second_thread'],account=native['second_account'])['id']==first['id']
    assert len(em_pool._items(cli,db))==1


@pytest.mark.skipif(not em_pool.available(), reason="schedule-reminder base not installed")
def test_prior_address_scoped_threads_accept_the_same_accounts_slug(tmp_path):
    native=fixtures.native_pool_case()
    case=native['first']
    cli=em_pool.default_reminder_path()
    db=str(tmp_path/'legacy-address.sqlite3')
    thread=case['ext']['x_email_monitor_thread_key']
    first=em_pool.upsert(cli,db,case['ext']['x_email_monitor_message_id'],thread,case['title'],
        ext_extra={'account':native['first_address']})['item']
    result=em_pool.upsert(cli,db,native['next_message'],thread,case['title'],ext_extra={
        'account':case['ext']['x_email_monitor_account'],'account_user':native['first_address']})
    assert result['action']=='merged' and result['item']['id']==first['id']
    assert len(em_pool._items(cli,db))==1


@pytest.mark.parametrize('explicit_db',[False,True])
def test_pool_cli_rejects_unproven_destination_before_owner_call(tmp_path,monkeypatch,capsys,explicit_db):
    import json
    case=fixtures.pool_merge_case()
    args=['em_pool.py']
    if explicit_db:
        args+=['--db',str(tmp_path/'public.db')]
    args+=['upsert','--message-id',case['ext']['x_email_monitor_message_id'],
           '--thread-key',case['ext']['x_email_monitor_thread_key'],'--title',case['title']]
    monkeypatch.setattr(sys,'argv',args)
    monkeypatch.setattr(em_pool,'upsert',lambda *a,**kw:pytest.fail('owner call reached'))
    assert em_pool._cli()==1
    assert json.loads(capsys.readouterr().err)['error_code']=='ERR_DATA_BOUNDARY'
    assert not (tmp_path/'public.db').exists()
