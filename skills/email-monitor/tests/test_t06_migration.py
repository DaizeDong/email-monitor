"""Already-migrated classifier retains one controller call and explicit user chains."""
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'scripts'))
# Imports are fake too, so this suite cannot initialize any live llmcall state.
sys.modules.setdefault('llmcall',SimpleNamespace(call=lambda *a,**kw:None))
import em_agent_classify as classifier


@pytest.mark.parametrize('chain', [None,['synthetic-route']])
@pytest.mark.parametrize('error', ['timeout','invalid output','unknown effects'])
def test_classification_failure_is_one_call(monkeypatch,chain,error):
    calls=[]
    class Failed:
        text=''
        data=None
        provider=None
        def __bool__(self): return False
    failed=Failed()
    failed.error=error
    monkeypatch.setattr(classifier,'_llmcall',lambda *a,**kw:calls.append(kw) or failed)
    assert classifier.classify({'sender':'user1@example.com','subject':'synthetic','body':'synthetic'},chain=chain) is None
    assert len(calls)==1
    assert calls[0]['chain']==chain
    assert not {'model','effort'} & calls[0].keys()
