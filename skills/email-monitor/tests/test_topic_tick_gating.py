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
    flag at all. Adding a label and hiding a message are different decisions.

    Must cover `_label_add`, the function that actually builds the argv, not just
    `topic_label`, its caller: `_label_add` is defined earlier in the file, so a
    slice starting at `def topic_label` misses it entirely, and a regression that
    adds `--archive` only to `_label_add` would leave a topic_label-only slice
    green. The check is narrowed to the literal `--archive` flag (rather than also
    matching the bare substring `archive(`) because `_label_add`'s own docstring
    names `archive()` in prose when explaining that it is that function's narrow
    sibling; matching on `archive(` would fail on that prose with no code defect
    present."""
    src = open(os.path.join(SCRIPTS, "em_tick.py"), encoding="utf-8").read()
    assert "def _label_add" in src and "def topic_label" in src
    start = src.index("def _label_add")
    after_label_add = src[start:]
    tl_idx = after_label_add.index("def topic_label")
    after_topic_label = after_label_add[tl_idx:]
    end = after_topic_label.index("\ndef ")
    body = after_label_add[:tl_idx] + after_topic_label[:end]
    assert "--archive" not in body


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


def test_topic_verdicts_are_counted_not_just_successes(monkeypatch, capsys):
    """unsure and failed must reach the log, because both write nothing.

    `topic_labeled=N` alone cannot separate "the gate is calibrated and this mail
    genuinely has no label" from "the gate refuses everything" from "the model
    chain is down", and those need three different responses. This drives one
    message into each state and asserts all three counts appear, so a regression
    that logs only successes fails here rather than four days later when someone
    tries to review the run and finds the number missing.
    """
    import em_tick

    cfg = {"taxonomy": "t", "sender_map": {"by_address": {}},
           "allowed_labels": ["Alpha"], "type_labels": []}
    monkeypatch.setattr(em_tick.em_topic, "load_config", lambda slug, log=None: cfg)
    monkeypatch.setattr(em_tick, "_make_transport", lambda *a, **k: (lambda **kw: None))

    states = iter(["decided", "unsure", "failed"])
    monkeypatch.setattr(em_tick.em_topic, "judge",
                        lambda *a, **k: {"state": next(states),
                                         "labels": [{"label": "Alpha", "evidence": "e"}],
                                         "dropped": [], "reason": ""})
    added = []
    monkeypatch.setattr(em_tick, "_label_add",
                        lambda u, mid, label, dry, app_pw=None: added.append(label) or True)

    records = [{"from": "a@example.com", "subject": "s%d" % i, "message_id": str(i)}
               for i in range(3)]
    n = em_tick.topic_label("u@example.com", "acct", records, dry=True)

    out = capsys.readouterr().out
    assert "judged=3" in out, out
    assert "decided=1" in out and "unsure=1" in out and "failed=1" in out, out
    assert "labels_added=1" in out, out
    assert n == 1 and added == ["Alpha"]


def test_verdict_counter_stays_silent_when_there_is_nothing_to_judge():
    """No records means no line. A counter that prints zeros every five minutes
    trains the reader to skip it, which is how the real one gets missed."""
    import em_tick
    assert em_tick.topic_label("u@example.com", "acct", [], dry=True) == 0
