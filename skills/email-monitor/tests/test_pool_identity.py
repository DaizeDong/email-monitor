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


def test_failed_pool_write_keeps_cursor_and_dry_run_has_no_writes(monkeypatch,tmp_path):
    import em_tick
    account,message,verdict=fixtures.pool_tick_case()
    writes=[]
    monkeypatch.setattr(em_tick,'resolve_app_pw',lambda *a:'synthetic-auth')
    monkeypatch.setattr(em_tick,'log',lambda *a:None)
    monkeypatch.setattr(em_tick.em_watch,'load_state',lambda *a:{'cursors':{},'seen_gm_msgids':[]})
    monkeypatch.setattr(em_tick.em_watch,'run_once',lambda *a:([message],{'last_uid':2,'uidvalidity':1}))
    monkeypatch.setattr(em_tick.em_watch,'save_state',lambda *a:writes.append('cursor'))
    monkeypatch.setattr(em_tick.em_alert,'send',lambda *a:writes.append('alert'))
    monkeypatch.setattr(em_tick,'classify_records_parallel',lambda *a:[verdict])
    def failed(*a,**kw):raise em_pool.PoolError('ERR_BUSY','synthetic failure')
    monkeypatch.setattr(em_pool,'upsert',failed)
    result=em_tick.process_account(account,{},'cli',None,None,str(tmp_path),False)
    assert result['error']=='pool_write_failed' and writes==[]
    result=em_tick.process_account(account,{},'cli',None,None,str(tmp_path),True)
    assert 'error' not in result and writes==[]


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
