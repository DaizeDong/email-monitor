"""Topic labeling must be off unless switched on, and must never de-inbox."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")


def test_topic_labeling_defaults_to_disabled():
    src = open(os.path.join(SCRIPTS, "em_tick.py"), encoding="utf-8").read()
    assert "topic_labeling" in src
    assert re.search(r'topic_labeling[^\n]*get\("enabled",\s*False\)', src), \
        "the flag must default to False, so an uninitialised machine stays inert"


def test_topic_path_never_archives():
    """Structural assertion: the topic write path must not reference the archive
    helper at all. Adding a label and hiding a message are different decisions."""
    src = open(os.path.join(SCRIPTS, "em_tick.py"), encoding="utf-8").read()
    block = src[src.index("def topic_label"):] if "def topic_label" in src else ""
    assert block, "expected a dedicated topic_label function"
    body = block.split("\ndef ")[0]
    assert "--archive" not in body and "archive(" not in body


def test_transport_closure_maps_a_dead_chain_to_failed(monkeypatch):
    """A falsy Result must become None, so judge reports an outage rather than a
    taxonomy problem. Without this mapping the two states collapse and the operator
    cannot tell 'the model is down' from 'my labels are ambiguous'."""
    class DeadResult:
        data = None
        def __bool__(self):
            return False

    import em_tick
    monkeypatch.setattr(em_tick.llmcall, "call", lambda *a, **k: DeadResult())
    call = em_tick._make_transport(5)
    assert call(prompt="anything") is None
