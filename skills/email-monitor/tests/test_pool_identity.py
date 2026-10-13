import copy
import importlib.util
from pathlib import Path
import sys

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import em_pool
spec=importlib.util.spec_from_file_location('identity_fixtures',SCRIPTS.parents[2]/'tools/make_fixtures.py')
fixtures=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


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
