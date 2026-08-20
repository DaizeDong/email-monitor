# Topic Labeling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the skill one judgement kernel that answers "what is this mail about", used identically by the incremental watcher and by a batch pass over historical mail, so the labelling standard cannot exist in two drifting copies.

**Architecture:** A pure-function kernel (`em_topic.py`) with a four stage pipeline: deterministic sender pre-gate, model judgement over sender plus subject, machine verification that each proposed label's evidence literally occurs in the input, and a three state verdict where two of the three states write nothing. Two thin callers consume it: `em_tick.py` for new mail and `em_relabel.py` for history. The taxonomy and sender map live only in the private companion config, resolved through the existing `tools/datadir.py`.

**Tech Stack:** Python 3, stdlib only in the kernel. Model transport via the existing `llmcall` package (the same one `em_agent_classify.py` uses). IMAP via `imaplib`. Tests via `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-20-topic-labeling-design.md`

## Global Constraints

- **Never write an en dash or em dash** anywhere in code, comments, docs, or commit messages. `tools/dash_guard.py` enforces this in pre-commit and CI.
- **Never put real personal data in this repo.** All fixtures use `example.com` / `example.org` senders and fictional names. `tools/pii_guard.py` enforces this. The taxonomy naming real institutions is DATA and belongs only in the private config.
- **Never remove the `\Inbox` label.** Topic labeling adds labels only, regardless of any archive setting.
- **Never use `--no-verify`** on commit or push, and never pipe a commit or push through a command that truncates output or masks the exit code.
- **Uninitialised means inert.** With no private config present, every entry point in this plan must be a silent no-op that exits 0, never an error and never a repo-internal fallback.
- **Run every command from the repository root.** Paths here are repo relative on purpose: an absolute path carries a username into a public repo, which `tools/pii_guard.py` rejects.
- Every new module goes in `skills/email-monitor/scripts/`, every new test in `skills/email-monitor/tests/`.
- Data classes per `.dataclass.json`: new scripts and tests are TOOL and FIXTURE. Anything a real run produces resolves through `tools/datadir.py`.

---

### Task 1: Evidence verification gate

The highest value gate and the most testable. A proposed label must quote a span that literally occurs in the sender or subject; a label whose evidence cannot be located is dropped.

**Files:**
- Create: `skills/email-monitor/scripts/em_topic.py`
- Test: `skills/email-monitor/tests/test_topic_evidence.py`

**Interfaces:**
- Consumes: nothing
- Produces: `normalize_span(s: str) -> str`, `evidence_holds(evidence: str, msg: dict) -> bool`, `verify_labels(proposed: list[dict], msg: dict) -> tuple[list[dict], list[dict]]` returning `(kept, dropped)` where each item is `{"label": str, "evidence": str, "source": str}` and each dropped item gains `"drop_reason": str`.

- [ ] **Step 1: Write the failing test**

```python
# skills/email-monitor/tests/test_topic_evidence.py
"""The evidence gate must be able to reject. A test suite that only feeds it
valid evidence proves nothing: a gate that returns True unconditionally would
pass every such test."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_topic  # noqa: E402

MSG = {
    "from": "Billing <billing@example.com>",
    "subject": "Your payment of $42.00 has been processed",
}


def test_span_present_in_subject_holds():
    assert em_topic.evidence_holds("payment of $42.00 has been processed", MSG)


def test_span_present_in_from_holds():
    assert em_topic.evidence_holds("billing@example.com", MSG)


def test_span_absent_is_rejected():
    assert not em_topic.evidence_holds("refund issued", MSG)


def test_paraphrase_is_rejected():
    """The model paraphrasing instead of quoting is the common failure, and it
    must not pass. This is the negative control for the whole gate."""
    assert not em_topic.evidence_holds("a payment was processed", MSG)


def test_empty_evidence_is_rejected():
    assert not em_topic.evidence_holds("", MSG)
    assert not em_topic.evidence_holds(None, MSG)


def test_case_and_whitespace_are_normalised():
    assert em_topic.evidence_holds("YOUR   PAYMENT of $42.00", MSG)


def test_verify_labels_splits_kept_from_dropped():
    proposed = [
        {"label": "Receipt", "evidence": "has been processed", "source": "model"},
        {"label": "Travel", "evidence": "flight to Boston", "source": "model"},
    ]
    kept, dropped = em_topic.verify_labels(proposed, MSG)
    assert [k["label"] for k in kept] == ["Receipt"]
    assert [d["label"] for d in dropped] == ["Travel"]
    assert dropped[0]["drop_reason"]
```

- [ ] **Step 2: Run test to verify it fails**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_topic_evidence.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'em_topic'`

- [ ] **Step 3: Write minimal implementation**

```python
# skills/email-monitor/scripts/em_topic.py
#!/usr/bin/env python3
"""Topic labeling kernel: decide what a message is about, and refuse to decide
when the evidence does not support it.

Stage 3 of the pipeline lives here first because it is the load bearing gate.
A model's self reported confidence is least reliable exactly where it has the
least information. Requiring it to quote a span that actually occurs in the
input is a far harder thing to fake than a number, and it is checkable by a
string operation rather than by trusting the model about itself.
"""
import re

_WS = re.compile(r"\s+")


def normalize_span(s):
    """Fold case and collapse whitespace so quoting differences in spacing do
    not defeat an otherwise genuine citation."""
    if not s:
        return ""
    return _WS.sub(" ", str(s)).strip().lower()


def _haystack(msg):
    return normalize_span("%s %s" % (msg.get("from", ""), msg.get("subject", "")))


def evidence_holds(evidence, msg):
    """True iff `evidence` occurs literally (modulo case and whitespace) in the
    message's sender or subject."""
    needle = normalize_span(evidence)
    if not needle:
        return False
    return needle in _haystack(msg)


def verify_labels(proposed, msg):
    """Partition proposed labels into those whose evidence checks out and those
    whose does not. Dropping is silent to the mailbox but never silent to the
    log: each dropped label carries why."""
    kept, dropped = [], []
    for item in proposed or []:
        ev = item.get("evidence")
        if evidence_holds(ev, msg):
            kept.append(dict(item))
        else:
            d = dict(item)
            d["drop_reason"] = ("evidence span not found in From or Subject: %r"
                                % (ev if ev else "<empty>"))
            dropped.append(d)
    return kept, dropped
```

- [ ] **Step 4: Run test to verify it passes**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_topic_evidence.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Prove the gate can fail by breaking it deliberately**

Temporarily edit `evidence_holds` to `return True`, rerun the tests, and confirm `test_span_absent_is_rejected`, `test_paraphrase_is_rejected` and `test_empty_evidence_is_rejected` FAIL. Then revert. A gate whose tests still pass when the gate is disabled is not testing the gate.

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_topic_evidence.py -v`
Expected after reverting: PASS, 7 passed

- [ ] **Step 6: Commit**

```bash
git add skills/email-monitor/scripts/em_topic.py skills/email-monitor/tests/test_topic_evidence.py
git commit -m "feat: evidence verification gate for topic labels

A proposed label must quote a span that literally occurs in the sender or
subject. Paraphrase, invention and empty evidence are all rejected. The test
suite includes the negative controls, so a gate stubbed to always pass fails
the suite."
```

---

### Task 2: Deterministic sender pre-gate

Mapped senders resolve without reaching the model. This is simultaneously a precision control and a cost control: a message that never reaches the model cannot be mislabelled by it.

**Files:**
- Modify: `skills/email-monitor/scripts/em_topic.py`
- Test: `skills/email-monitor/tests/test_topic_pregate.py`

**Interfaces:**
- Consumes: `normalize_span` from Task 1
- Produces: `sender_address(frm: str) -> str`, `pregate(msg: dict, sender_map: dict) -> list[dict] | None` returning label items with `source="map"`, or `None` when nothing matched.

The `sender_map` shape, which Task 6 also compiles to filter XML:

```python
{
  "version": 1,
  "by_address": {"noreply@shop.example.com": "Accounts/Shopping"},
  "by_domain":  {"news.example.org": "Promo"},
  "by_list_id": {"users.lists.example.net": "Rutgers/RUNews"}
}
```

- [ ] **Step 1: Write the failing test**

```python
# skills/email-monitor/tests/test_topic_pregate.py
"""The pre-gate must resolve mapped senders WITHOUT the model, and must decline
cleanly when nothing matches. Asserting on the return value is not enough: the
point of the gate is that the model is never called, so that is asserted too in
Task 3's integration test."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_topic  # noqa: E402

SENDER_MAP = {
    "version": 1,
    "by_address": {"noreply@shop.example.com": "Accounts/Shopping"},
    "by_domain": {"news.example.org": "Promo"},
    "by_list_id": {"users.lists.example.net": "Rutgers/RUNews"},
}


def test_address_match_wins():
    msg = {"from": "Shop <noreply@shop.example.com>", "subject": "Order shipped"}
    got = em_topic.pregate(msg, SENDER_MAP)
    assert got == [{"label": "Accounts/Shopping", "evidence": "noreply@shop.example.com",
                    "source": "map"}]


def test_domain_match_when_no_address_match():
    msg = {"from": "Digest <weekly@news.example.org>", "subject": "This week"}
    got = em_topic.pregate(msg, SENDER_MAP)
    assert got[0]["label"] == "Promo"
    assert got[0]["source"] == "map"


def test_list_id_match():
    msg = {"from": "Someone <a@example.net>", "subject": "Notice",
           "list_id": "Campus notices <users.lists.example.net>"}
    got = em_topic.pregate(msg, SENDER_MAP)
    assert got[0]["label"] == "Rutgers/RUNews"


def test_unmapped_sender_declines():
    msg = {"from": "Stranger <who@unknown.example.com>", "subject": "Hello"}
    assert em_topic.pregate(msg, SENDER_MAP) is None


def test_empty_map_declines():
    msg = {"from": "Shop <noreply@shop.example.com>", "subject": "Order shipped"}
    assert em_topic.pregate(msg, {}) is None


def test_pregate_evidence_survives_verification():
    """A mapped label must still satisfy the Task 1 gate, so the two stages
    cannot disagree about what counts as evidence."""
    msg = {"from": "Shop <noreply@shop.example.com>", "subject": "Order shipped"}
    kept, dropped = em_topic.verify_labels(em_topic.pregate(msg, SENDER_MAP), msg)
    assert len(kept) == 1 and not dropped


def test_sender_address_parsing():
    assert em_topic.sender_address("A B <x@example.com>") == "x@example.com"
    assert em_topic.sender_address("x@example.com") == "x@example.com"
    assert em_topic.sender_address("") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_topic_pregate.py -v`
Expected: FAIL with `AttributeError: module 'em_topic' has no attribute 'pregate'`

- [ ] **Step 3: Write minimal implementation**

Append to `skills/email-monitor/scripts/em_topic.py`:

```python
_ADDR = re.compile(r"<([^>]+)>")
_BARE = re.compile(r"[\w.\-+]+@[\w.\-]+")


def sender_address(frm):
    """Extract the bare address from a From header value."""
    if not frm:
        return ""
    m = _ADDR.search(frm) or _BARE.search(frm)
    return (m.group(1) if m else "").strip().lower()


def _list_identity(msg):
    raw = msg.get("list_id") or ""
    m = _ADDR.search(raw)
    return (m.group(1) if m else raw).strip().lower()


def pregate(msg, sender_map):
    """Resolve a message deterministically, or decline.

    Order is address, then domain, then list identity: the most specific
    statement about a sender wins. Returns None rather than an empty list when
    nothing matches, so a caller cannot confuse "mapped to nothing" with
    "not mapped".
    """
    if not sender_map:
        return None
    addr = sender_address(msg.get("from"))
    if addr:
        label = (sender_map.get("by_address") or {}).get(addr)
        if label:
            return [{"label": label, "evidence": addr, "source": "map"}]
        domain = addr.split("@")[-1]
        label = (sender_map.get("by_domain") or {}).get(domain)
        if label:
            return [{"label": label, "evidence": domain, "source": "map"}]
    lid = _list_identity(msg)
    if lid:
        label = (sender_map.get("by_list_id") or {}).get(lid)
        if label:
            return [{"label": label, "evidence": lid, "source": "map"}]
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_topic_pregate.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add skills/email-monitor/scripts/em_topic.py skills/email-monitor/tests/test_topic_pregate.py
git commit -m "feat: deterministic sender pre-gate for topic labels

Mapped senders resolve by address, domain or list identity without reaching
the model. Declines with None rather than an empty list so callers cannot
confuse mapped-to-nothing with not-mapped. Pre-gate evidence is checked by the
same verifier the model path uses."
```

---

### Task 3: Three state verdict and the model path

Assembles the pipeline. Two of the three states write nothing, and they are distinguished because a persistent `failed` rate is an outage while a persistent `unsure` rate is a taxonomy problem.

**Files:**
- Modify: `skills/email-monitor/scripts/em_topic.py`
- Test: `skills/email-monitor/tests/test_topic_judge.py`

**Interfaces:**
- Consumes: `pregate`, `verify_labels` from Tasks 1 and 2
- Produces: `judge(msg, taxonomy, sender_map, allowed_labels, call=None, log=None) -> dict` returning `{"state": "decided"|"unsure"|"failed", "labels": [...], "dropped": [...], "reason": str}`. The `call` parameter is the model transport, injected so tests never touch the network; production passes a closure over `llmcall.call`.

- [ ] **Step 1: Write the failing test**

```python
# skills/email-monitor/tests/test_topic_judge.py
"""judge() must abstain in every direction it can be wrong: a broken call, an
unparseable reply, a label outside the taxonomy, and a label whose evidence does
not check out. All four write nothing."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_topic  # noqa: E402

TAXONOMY = "Receipt means proof that money already moved."
ALLOWED = ["Receipt", "Promo", "Accounts/Shopping"]
SENDER_MAP = {"version": 1, "by_address": {"noreply@shop.example.com": "Accounts/Shopping"}}
MSG = {"from": "Billing <billing@example.com>",
       "subject": "Your payment of $42.00 has been processed"}


def test_mapped_sender_never_calls_the_model():
    calls = []

    def spy(**kw):
        calls.append(kw)
        return {"labels": [{"label": "Promo", "evidence": "whatever"}]}

    msg = {"from": "Shop <noreply@shop.example.com>", "subject": "Order shipped"}
    got = em_topic.judge(msg, TAXONOMY, SENDER_MAP, ALLOWED, call=spy)
    assert got["state"] == "decided"
    assert [l["label"] for l in got["labels"]] == ["Accounts/Shopping"]
    assert calls == [], "a mapped sender must not reach the model"


def test_good_model_reply_is_decided():
    def call(**kw):
        return {"labels": [{"label": "Receipt", "evidence": "has been processed"}]}

    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED, call=call)
    assert got["state"] == "decided"
    assert got["labels"][0]["label"] == "Receipt"
    assert got["labels"][0]["source"] == "model"


def test_transport_failure_is_failed_and_writes_nothing():
    def call(**kw):
        raise RuntimeError("all providers exhausted")

    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED, call=call)
    assert got["state"] == "failed"
    assert got["labels"] == []


def test_none_reply_is_failed():
    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED, call=lambda **kw: None)
    assert got["state"] == "failed"
    assert got["labels"] == []


def test_label_outside_taxonomy_is_dropped():
    def call(**kw):
        return {"labels": [{"label": "NotARealLabel", "evidence": "payment"}]}

    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED, call=call)
    assert got["state"] == "unsure"
    assert got["labels"] == []
    assert got["dropped"][0]["label"] == "NotARealLabel"


def test_unverifiable_evidence_yields_unsure_not_decided():
    def call(**kw):
        return {"labels": [{"label": "Receipt", "evidence": "a refund was issued"}]}

    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED, call=call)
    assert got["state"] == "unsure"
    assert got["labels"] == []
    assert got["dropped"][0]["drop_reason"]


def test_empty_label_list_is_unsure():
    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED,
                         call=lambda **kw: {"labels": []})
    assert got["state"] == "unsure"
    assert got["labels"] == []


def test_partial_survival_is_decided_with_the_survivor():
    def call(**kw):
        return {"labels": [
            {"label": "Receipt", "evidence": "has been processed"},
            {"label": "Promo", "evidence": "limited time offer"},
        ]}

    got = em_topic.judge(MSG, TAXONOMY, SENDER_MAP, ALLOWED, call=call)
    assert got["state"] == "decided"
    assert [l["label"] for l in got["labels"]] == ["Receipt"]
    assert [d["label"] for d in got["dropped"]] == ["Promo"]
```

- [ ] **Step 2: Run test to verify it fails**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_topic_judge.py -v`
Expected: FAIL with `AttributeError: module 'em_topic' has no attribute 'judge'`

- [ ] **Step 3: Write minimal implementation**

Append to `skills/email-monitor/scripts/em_topic.py`:

```python
PROMPT = """You label one email by topic. Sender and subject only, never a body.

STANDARD (the only standard; do not invent labels or reinterpret these):
%(taxonomy)s

ALLOWED LABELS (copy byte for byte; anything else is discarded):
%(allowed)s

MESSAGE
From: %(frm)s
Subject: %(subj)s
Date: %(date)s

RULES
- Judge THIS message from ITS OWN sender and subject. Never reason "this sender
  is usually X so this one is X".
- Omission over commission: a missing label costs nothing, a wrong label is the
  defect being fixed. When the evidence is not plain, return an empty list.
- For every label you propose you MUST quote an "evidence" span copied VERBATIM
  from the From or Subject above. Not a paraphrase, not a summary. A label whose
  evidence cannot be found in the input is discarded automatically.

Reply with JSON only:
{"labels": [{"label": "<one of the allowed labels>", "evidence": "<verbatim span>"}]}
An empty list is a valid and often correct answer.
"""


def build_prompt(msg, taxonomy, allowed_labels):
    return PROMPT % {
        "taxonomy": taxonomy or "(no taxonomy configured)",
        "allowed": "\n".join("- %s" % l for l in allowed_labels),
        "frm": msg.get("from", ""),
        "subj": msg.get("subject", ""),
        "date": msg.get("date", ""),
    }


def _verdict(state, labels=None, dropped=None, reason=""):
    return {"state": state, "labels": labels or [], "dropped": dropped or [],
            "reason": reason}


def judge(msg, taxonomy, sender_map, allowed_labels, call=None, log=None):
    """Decide a message's topic labels, or decline.

    Three states, because "the call broke" and "the model judged but did not
    clear the bar" need different responses from the operator even though both
    write nothing to the mailbox.
    """
    mapped = pregate(msg, sender_map)
    if mapped:
        kept, dropped = verify_labels(mapped, msg)
        if kept:
            return _verdict("decided", kept, dropped, "sender map")
        return _verdict("unsure", [], dropped, "sender map evidence did not verify")

    if call is None:
        return _verdict("failed", reason="no transport supplied")

    try:
        reply = call(prompt=build_prompt(msg, taxonomy, allowed_labels))
    except Exception as exc:                     # transport is allowed to fail
        if log:
            log("topic judge transport failed: %s" % exc)
        return _verdict("failed", reason="transport error: %s" % exc)

    if not isinstance(reply, dict) or not isinstance(reply.get("labels"), list):
        return _verdict("failed", reason="unparseable model reply")

    allowed = set(allowed_labels)
    proposed, dropped = [], []
    for item in reply["labels"]:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if label not in allowed:
            dropped.append({"label": label, "evidence": item.get("evidence"),
                            "source": "model",
                            "drop_reason": "label not in this account's allowed set"})
            continue
        proposed.append({"label": label, "evidence": item.get("evidence"),
                         "source": "model"})

    kept, ev_dropped = verify_labels(proposed, msg)
    dropped.extend(ev_dropped)
    if kept:
        return _verdict("decided", kept, dropped, "model")
    return _verdict("unsure", [], dropped, "no proposed label survived verification")
```

- [ ] **Step 4: Run test to verify it passes**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_topic_judge.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Run the whole existing suite to confirm nothing regressed**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/ -q`
Expected: all pre-existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add skills/email-monitor/scripts/em_topic.py skills/email-monitor/tests/test_topic_judge.py
git commit -m "feat: three state topic verdict with injected transport

decided writes labels; unsure and failed write nothing and are distinguished
because a persistent failed rate is an outage while a persistent unsure rate is
a taxonomy problem. Transport is injected so the tests never touch the network,
and one test asserts a mapped sender produces zero model calls."
```

---

### Task 4: Config loading, and inert when uninitialised

**Files:**
- Modify: `skills/email-monitor/scripts/em_topic.py`
- Test: `skills/email-monitor/tests/test_topic_config.py`

**Interfaces:**
- Consumes: `tools/datadir.py` (`data_path`, `DataDirNotInitialized`)
- Produces: `load_config(account_slug: str) -> dict | None` returning `{"taxonomy": str, "sender_map": dict, "allowed_labels": list[str]}` or `None` when the private config is absent or incomplete.

- [ ] **Step 1: Write the failing test**

```python
# skills/email-monitor/tests/test_topic_config.py
"""Uninitialised must mean inert, not broken, and never a repo-internal
fallback. This repo has already leaked once through a documented in-repo
fallback path, so the absence of config must return None, and the repo must
contain no taxonomy of its own for anything to fall back to."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_topic  # noqa: E402


def test_missing_config_returns_none_not_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(tmp_path / "nonexistent"))
    assert em_topic.load_config("dz") is None


def test_partial_config_returns_none(tmp_path, monkeypatch):
    """A taxonomy with no allowed label set is not a usable config. Half a
    config must be treated as no config, not as a config with defaults."""
    d = tmp_path / "cfg" / "rules"
    d.mkdir(parents=True)
    (d / "taxonomy.md").write_text("Receipt means money moved.", encoding="utf-8")
    monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(tmp_path / "cfg"))
    assert em_topic.load_config("dz") is None


def test_complete_config_loads(tmp_path, monkeypatch):
    d = tmp_path / "cfg" / "rules"
    d.mkdir(parents=True)
    (d / "taxonomy.md").write_text("Receipt means money moved.", encoding="utf-8")
    (d / "sender_map.json").write_text(json.dumps(
        {"version": 1, "by_address": {"a@example.com": "Receipt"}}), encoding="utf-8")
    (d / "labels.json").write_text(json.dumps({"dz": ["Receipt", "Promo"]}),
                                   encoding="utf-8")
    monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(tmp_path / "cfg"))
    cfg = em_topic.load_config("dz")
    assert cfg["allowed_labels"] == ["Receipt", "Promo"]
    assert cfg["sender_map"]["by_address"]["a@example.com"] == "Receipt"
    assert "money moved" in cfg["taxonomy"]


def test_unknown_account_returns_none(tmp_path, monkeypatch):
    d = tmp_path / "cfg" / "rules"
    d.mkdir(parents=True)
    (d / "taxonomy.md").write_text("x", encoding="utf-8")
    (d / "sender_map.json").write_text("{}", encoding="utf-8")
    (d / "labels.json").write_text(json.dumps({"dz": ["Receipt"]}), encoding="utf-8")
    monkeypatch.setenv("EMAIL_MONITOR_CONFIG_DIR", str(tmp_path / "cfg"))
    assert em_topic.load_config("no_such_account") is None


def test_repo_ships_no_taxonomy_to_fall_back_to():
    """The structural half of the same rule: there must be nothing in the repo
    that a future in-repo fallback could point at."""
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, check=True).stdout.strip()
    tracked = subprocess.run(["git", "ls-files"], cwd=root,
                             capture_output=True, text=True, check=True).stdout.split()
    offenders = [p for p in tracked
                 if os.path.basename(p) in ("taxonomy.md", "sender_map.json", "labels.json")]
    assert offenders == [], "topic config must live only in the private companion repo"
```

- [ ] **Step 2: Run test to verify it fails**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_topic_config.py -v`
Expected: FAIL with `AttributeError: module 'em_topic' has no attribute 'load_config'`

- [ ] **Step 3: Write minimal implementation**

Append to `skills/email-monitor/scripts/em_topic.py`:

```python
import json
import os

SKILL = "email-monitor"


def _config_dir():
    """Resolve the private config dir. Deliberately no in-repo fallback: a
    fallback into this repo is exactly how a real address once landed in a
    public tree under the name "legacy fallback"."""
    env = os.environ.get("EMAIL_MONITOR_CONFIG_DIR")
    if env:
        return os.path.expanduser(env)
    return os.path.expanduser(os.path.join("~", ".%s-config" % SKILL))


def load_config(account_slug):
    """Return the topic config for one account, or None if this machine has not
    been initialised for topic labeling. Never raises, never falls back."""
    base = os.path.join(_config_dir(), "rules")
    tax_p = os.path.join(base, "taxonomy.md")
    map_p = os.path.join(base, "sender_map.json")
    lab_p = os.path.join(base, "labels.json")
    if not (os.path.isfile(tax_p) and os.path.isfile(map_p) and os.path.isfile(lab_p)):
        return None
    try:
        taxonomy = open(tax_p, encoding="utf-8").read()
        sender_map = json.load(open(map_p, encoding="utf-8"))
        labels = json.load(open(lab_p, encoding="utf-8"))
    except Exception:
        return None
    allowed = labels.get(account_slug)
    if not allowed:
        return None
    return {"taxonomy": taxonomy, "sender_map": sender_map, "allowed_labels": allowed}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_topic_config.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add skills/email-monitor/scripts/em_topic.py skills/email-monitor/tests/test_topic_config.py
git commit -m "feat: topic config loading, inert when uninitialised

Missing or partial config returns None rather than raising or defaulting, so an
uninitialised machine is silent rather than broken. A structural test asserts
the repo tracks no taxonomy file, closing the in-repo fallback path that caused
this repo's earlier leak."
```

---

### Task 5: Synthetic fixtures reproducing the four known failure shapes

**Files:**
- Modify: `tools/make_fixtures.py`
- Create: `skills/email-monitor/tests/topic_regression.jsonl` (generated, never hand edited)
- Test: `skills/email-monitor/tests/test_topic_regression.py`

**Interfaces:**
- Consumes: `judge` from Task 3
- Produces: a generated fixture file plus a test that runs the kernel against it

- [ ] **Step 1: Add the cases to the generator**

Append a `TOPIC_CASES` list and a second render target in `tools/make_fixtures.py`, following the existing `CASES` and `render()` pattern exactly. Every sender is fictional. Each case names the failure shape it reproduces:

```python
# tools/make_fixtures.py, appended
TOPIC_FIXTURE = os.path.join("skills", "email-monitor", "tests", "topic_regression.jsonl")

# Each case reproduces a SHAPE of failure observed in a real audit, using
# invented senders. The shapes, not the messages, are what must not regress.
TOPIC_CASES = [
    {
        "shape": "keyword-in-subject-is-not-the-topic",
        "from": "Hotel Front Desk <no-reply@hotel.example.com>",
        "subject": "Your temporary account password",
        "expect_labels": [],
        "why": "the word password is not a purchase; nothing here shows money moved",
    },
    {
        "shape": "receipt-of-documents-is-not-a-purchase",
        "from": "Graduate Admissions <admissions@school.example.org>",
        "subject": "Recommendation Confirmation of Receipt",
        "expect_labels": [],
        "why": "receipt here means documents arrived, not that a payment occurred",
    },
    {
        "shape": "wrong-domain-entirely",
        "from": "Paper Digest <digest@papers.example.org>",
        "subject": "Access to the project group was granted",
        "expect_labels": [],
        "why": "a paper recommendation service is not a code hosting platform, and "
               "no allowed label is plainly supported by sender or subject",
    },
    {
        "shape": "money-in-is-not-a-spend",
        "from": "Marketplace <noreply@market.example.com>",
        "subject": "You sold an item on the community market",
        "expect_labels": [],
        "why": "money arrived rather than left; a receipt proves a spend",
    },
    {
        "shape": "genuine-receipt-must-still-be-labelled",
        "from": "Billing <billing@shop.example.com>",
        "subject": "Receipt for your payment of $42.00",
        "expect_labels": ["Receipt"],
        "why": "positive control: if this one stops being labelled the gate is too tight",
    },
]
```

- [ ] **Step 2: Write the failing test**

```python
# skills/email-monitor/tests/test_topic_regression.py
"""Run the kernel against the generated synthetic regression set.

Note the positive control at the end of the case list: a suite made only of
"must not label" cases is passed trivially by a kernel that never labels
anything, which would be a useless kernel that scores perfectly."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_topic  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "topic_regression.jsonl")
TAXONOMY = ("Receipt: proof that money already left the account. An order "
            "confirmation without an amount, a declined transaction, a password "
            "mail, or an acknowledgement that documents were received are NOT "
            "receipts. Promo: pure marketing with no personal transaction.")
ALLOWED = ["Receipt", "Promo", "Accounts/Shopping"]


def rows():
    with open(FIXTURE, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def test_fixture_exists_and_is_populated():
    assert len(rows()) >= 5


def test_each_case_matches_expectation():
    """The model is not called here. Each case is driven through the kernel with
    a transport stub that returns the tempting wrong answer, so what is being
    tested is whether the gates reject it."""
    failures = []
    for r in rows():
        msg = {"from": r["from"], "subject": r["subject"]}
        tempting = [{"label": "Receipt", "evidence": r["subject"]}]

        def call(**kw):
            return {"labels": tempting}

        got = em_topic.judge(msg, TAXONOMY, {}, ALLOWED, call=call)
        labels = sorted(l["label"] for l in got["labels"])
        if labels != sorted(r["expect_labels"]):
            failures.append("%s: expected %s got %s" % (r["shape"], r["expect_labels"], labels))
    assert not failures, "\n".join(failures)


def test_fixture_is_generator_reproducible():
    """Byte equality with a fresh generator run. A real message cannot be
    reproduced by the generator, so pasting one in fails here."""
    import subprocess
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, check=True).stdout.strip()
    before = open(FIXTURE, "rb").read()
    subprocess.run([sys.executable, os.path.join(root, "tools", "make_fixtures.py")],
                   cwd=root, check=True, capture_output=True)
    assert open(FIXTURE, "rb").read() == before
```

- [ ] **Step 3: Run test to verify it fails**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_topic_regression.py -v`
Expected: FAIL, fixture file does not exist

- [ ] **Step 4: Generate the fixture and rerun**

Run (from the repo root): `python tools/make_fixtures.py && python -m pytest skills/email-monitor/tests/test_topic_regression.py -v`
Expected: PASS, 3 passed

Note on the expected behaviour being tested: for the four negative cases the stub proposes `Receipt` quoting the whole subject, which passes the evidence gate (it is verbatim) but must still be rejected. Rejection therefore has to come from the taxonomy in the prompt rather than from a string check, which is precisely why these cases are worth freezing: they are the ones a string gate cannot catch. If a case fails, the correct fix is to sharpen the taxonomy wording in the private config and the test constant together, not to loosen the assertion.

- [ ] **Step 5: Verify the data boundary gate still passes**

Run (from the repo root): `python tools/data_boundary.py > nul 2>&1 & echo boundary_rc=%errorlevel%`
Expected: `boundary_rc=0`, and the new fixture is recognised as generator reproducible

- [ ] **Step 6: Commit**

```bash
git add tools/make_fixtures.py skills/email-monitor/tests/topic_regression.jsonl skills/email-monitor/tests/test_topic_regression.py
git commit -m "test: synthetic regression set for the four known mislabel shapes

Fictional senders only, generated by make_fixtures.py and byte-equality checked,
so pasting a real message in fails loudly. Includes a positive control, because
a suite of only must-not-label cases is passed perfectly by a kernel that never
labels anything."
```

---

### Task 6: Compile sender_map to Gmail filter XML

One source of truth feeding two consumers, so the filters and the kernel cannot disagree.

**Files:**
- Create: `skills/email-monitor/scripts/em_filters.py`
- Test: `skills/email-monitor/tests/test_em_filters.py`

**Interfaces:**
- Consumes: the `sender_map` shape from Task 2
- Produces: `compile_filters(sender_map: dict) -> str` returning Atom XML importable by Gmail, and a `__main__` that writes it to a path given by `--out`.

- [ ] **Step 1: Write the failing test**

```python
# skills/email-monitor/tests/test_em_filters.py
"""The filters are GENERATED from sender_map.json, never hand written. That is
the whole point: a hand written filter is a second copy of the standard, and two
copies drift. These tests pin the properties that make the generated set safe."""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_filters  # noqa: E402

SENDER_MAP = {
    "version": 1,
    "by_address": {"a@example.com": "Receipt", "b@example.com": "Receipt",
                   "c@example.org": "Promo"},
    "by_domain": {"news.example.net": "Promo"},
}
NS = {"a": "http://www.w3.org/2005/Atom", "apps": "http://schemas.google.com/apps/2006"}


def entries(xml):
    return ET.fromstring(xml).findall("a:entry", NS)


def props(entry):
    return {p.get("name"): p.get("value")
            for p in entry.findall("apps:property", NS)}


def test_addresses_sharing_a_label_are_merged_into_one_filter():
    got = entries(em_filters.compile_filters(SENDER_MAP))
    receipt = [props(e) for e in got if props(e).get("label") == "Receipt"]
    assert len(receipt) == 1
    assert "a@example.com" in receipt[0]["from"]
    assert "b@example.com" in receipt[0]["from"]


def test_no_generated_filter_skips_the_inbox():
    """Generated filters label only. Hiding mail is a separate decision that is
    not the generator's to make."""
    for e in entries(em_filters.compile_filters(SENDER_MAP)):
        assert "shouldArchive" not in props(e)


def test_no_generated_filter_matches_on_free_text():
    """Every generated criterion is a from: clause. The keyword rules are the
    exact thing being retired, so the generator must be incapable of emitting
    one."""
    for e in entries(em_filters.compile_filters(SENDER_MAP)):
        p = props(e)
        assert "hasTheWord" not in p and "subject" not in p
        assert p.get("from")


def test_empty_map_yields_no_entries():
    assert entries(em_filters.compile_filters({})) == []


def test_output_parses_as_xml():
    ET.fromstring(em_filters.compile_filters(SENDER_MAP))
```

- [ ] **Step 2: Run test to verify it fails**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_em_filters.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'em_filters'`

- [ ] **Step 3: Write minimal implementation**

```python
# skills/email-monitor/scripts/em_filters.py
#!/usr/bin/env python3
"""Compile sender_map.json into Gmail filter XML.

The filters keep the job they are good at: deterministic sender to label
mapping, evaluated by Google, working on a phone and while this machine is off.
They lose the job they were bad at, guessing a topic from keywords, and this
generator is structurally incapable of emitting such a rule: it only ever writes
`from:` criteria.

Generated filters never archive. Hiding a message is a separate decision.
"""
import argparse
import collections
import json
import sys
from xml.sax.saxutils import escape

HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:apps="http://schemas.google.com/apps/2006">\n'
        '  <title>Mail Filters</title>\n')
TAIL = "</feed>\n"
MAXLEN = 3900     # a single from: clause stays comfortably inside Gmail's limit


def _groups(sender_map):
    by_label = collections.defaultdict(list)
    for addr, label in sorted((sender_map.get("by_address") or {}).items()):
        by_label[label].append(addr)
    for dom, label in sorted((sender_map.get("by_domain") or {}).items()):
        by_label[label].append("*@" + dom)
    return by_label


def compile_filters(sender_map):
    out = [HEAD]
    for label, senders in sorted(_groups(sender_map).items()):
        chunk, size = [], 0
        for s in senders:
            if size + len(s) + 1 > MAXLEN and chunk:
                out.append(_entry(label, chunk))
                chunk, size = [], 0
            chunk.append(s)
            size += len(s) + 1
        if chunk:
            out.append(_entry(label, chunk))
    out.append(TAIL)
    return "".join(out)


def _entry(label, senders):
    frm = escape("|".join(senders))
    return ('  <entry>\n'
            '    <category term="filter"></category>\n'
            '    <title>Mail Filter</title>\n'
            '    <content></content>\n'
            '    <apps:property name="from" value="%s"/>\n'
            '    <apps:property name="label" value="%s"/>\n'
            '  </entry>\n' % (frm, escape(label)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sender-map", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sm = json.load(open(a.sender_map, encoding="utf-8"))
    xml = compile_filters(sm)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(xml)
    n = xml.count("<entry>")
    print(json.dumps({"out": a.out, "entries": n}))
    if n == 0:
        print("WARNING: sender map produced zero filters", file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_em_filters.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add skills/email-monitor/scripts/em_filters.py skills/email-monitor/tests/test_em_filters.py
git commit -m "feat: compile sender_map to Gmail filter XML

One source feeds both the kernel pre-gate and the filters, so they cannot state
different things. The generator can only emit from: criteria and never sets
shouldArchive, making the retired keyword-and-hide rule shape unrepresentable."
```

---

### Task 7: Batch entry point over historical mail

**Files:**
- Create: `skills/email-monitor/scripts/em_relabel.py`
- Test: `skills/email-monitor/tests/test_em_relabel.py`

**Interfaces:**
- Consumes: `em_topic.judge`, `em_topic.load_config`
- Produces: `plan_changes(messages, verdicts) -> dict`, `assert_complete(messages, verdicts) -> None` (raises `IncompleteRun`), and a CLI `em_relabel.py --account <slug> [--since <date>] --dry|--commit`.

The IMAP mechanics (msgid location, grouped STORE, rollback snapshot, read back verification) are ported from the validated 2026-08 batch run. Tests cover the pure planning functions; the IMAP layer is exercised by the dry run against a live account, which is a manual verification step, not a unit test.

- [ ] **Step 1: Write the failing test**

```python
# skills/email-monitor/tests/test_em_relabel.py
"""Planning is pure and therefore tested; IMAP is not mocked, because a mocked
IMAP proves only that the mock behaves. The completeness assertion is the one
that matters most: a run that silently judges fewer messages than it read looks
exactly like a run with nothing to do."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import em_relabel  # noqa: E402
import pytest  # noqa: E402

MESSAGES = [
    {"msgid": "1", "from": "a@example.com", "subject": "x", "labels": ["Receipt"]},
    {"msgid": "2", "from": "b@example.com", "subject": "y", "labels": []},
]


def test_complete_run_passes():
    verdicts = {"1": {"state": "decided", "labels": [{"label": "Receipt"}]},
                "2": {"state": "unsure", "labels": []}}
    em_relabel.assert_complete(MESSAGES, verdicts)


def test_missing_verdict_raises():
    with pytest.raises(em_relabel.IncompleteRun):
        em_relabel.assert_complete(MESSAGES, {"1": {"state": "unsure", "labels": []}})


def test_unsure_and_failed_produce_no_changes():
    verdicts = {"1": {"state": "unsure", "labels": []},
                "2": {"state": "failed", "labels": []}}
    plan = em_relabel.plan_changes(MESSAGES, verdicts)
    assert plan["add"] == {} and plan["remove"] == {}


def test_add_only_for_labels_not_already_present():
    verdicts = {"1": {"state": "decided", "labels": [{"label": "Receipt"}]},
                "2": {"state": "decided", "labels": [{"label": "Promo"}]}}
    plan = em_relabel.plan_changes(MESSAGES, verdicts)
    assert plan["add"] == {"Promo": ["2"]}


def test_plan_never_removes_the_inbox_label():
    msgs = [{"msgid": "3", "from": "c@example.com", "subject": "z",
             "labels": ["Receipt", "\\Inbox"]}]
    verdicts = {"3": {"state": "decided", "labels": [{"label": "Promo"}]}}
    plan = em_relabel.plan_changes(msgs, verdicts)
    for ids in plan["remove"].values():
        assert "3" not in ids or "\\Inbox" not in plan["remove"]
    assert "\\Inbox" not in plan["remove"]
```

- [ ] **Step 2: Run test to verify it fails**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_em_relabel.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'em_relabel'`

- [ ] **Step 3: Write minimal implementation**

Create `skills/email-monitor/scripts/em_relabel.py` with the pure planning core first:

```python
#!/usr/bin/env python3
"""Batch topic labeling over historical mail. Judgement comes from em_topic;
this module only sequences, plans, and writes.

The additive posture is deliberate: this pass adds labels the kernel is
confident about and does not remove existing ones, because removal on a
historical corpus is the operation that can lose information the operator
curated by hand. Removal is available through the audited review flow, not as a
side effect of a routine pass.
"""
import json

SYSTEM_LABELS = {"\\Inbox", "\\Sent", "\\Draft", "\\Drafts", "\\Important",
                 "\\Starred", "\\Trash", "\\Junk", "\\Spam", "\\Muted", "\\Chat", "\\All"}


class IncompleteRun(Exception):
    """Raised when the verdict set does not cover every message that was read.
    A short run must fail loudly: silently judging fewer messages than were read
    is indistinguishable from a run that found nothing to do."""


def assert_complete(messages, verdicts):
    missing = [m["msgid"] for m in messages if m["msgid"] not in verdicts]
    if missing:
        raise IncompleteRun("%d of %d messages have no verdict (first: %s)"
                            % (len(missing), len(messages), missing[0]))


def plan_changes(messages, verdicts):
    """Turn verdicts into a label grouped plan. Only `decided` verdicts act."""
    add = {}
    for m in messages:
        v = verdicts.get(m["msgid"]) or {}
        if v.get("state") != "decided":
            continue
        have = set(m.get("labels") or [])
        for item in v.get("labels") or []:
            label = item["label"] if isinstance(item, dict) else item
            if label in SYSTEM_LABELS or label in have:
                continue
            add.setdefault(label, []).append(m["msgid"])
    return {"add": add, "remove": {}}
```

Then add the IMAP layer below it, porting the verified sequence: locate by `X-GM-MSGID`, write the rollback snapshot before any mutation, `STORE` grouped by label in batches under 1000, and read back every touched message asserting its label set equals the expected set.

- [ ] **Step 4: Run test to verify it passes**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_em_relabel.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add skills/email-monitor/scripts/em_relabel.py skills/email-monitor/tests/test_em_relabel.py
git commit -m "feat: batch topic labeling entry point

Additive by default: a routine pass adds confident labels and removes nothing,
because removal on a curated historical corpus is the lossy direction. A short
verdict set raises rather than shrinking the run."
```

---

### Task 8: Wire the incremental path and update every document

Folded together because a capability the operator cannot discover is not delivered, and this repo's checklist requires a documentation sweep before any change ships.

**Files:**
- Modify: `skills/email-monitor/scripts/em_tick.py`
- Modify: `skills/email-monitor/SKILL.md`, `README.md`, `README_CN.md`, `CONFIG.md`, `CHANGELOG.md`, `ROADMAP.md`
- Test: `skills/email-monitor/tests/test_topic_tick_gating.py`

**Interfaces:**
- Consumes: `em_topic.load_config`, `em_topic.judge`
- Produces: a `topic_labeling.enabled` config flag, default false

- [ ] **Step 1: Write the failing test**

```python
# skills/email-monitor/tests/test_topic_tick_gating.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/test_topic_tick_gating.py -v`
Expected: FAIL, `topic_labeling` not found in `em_tick.py`

- [ ] **Step 3: Add the gated step to em_tick.py**

Add a `topic_label(...)` function that calls `em_topic.load_config` once per account, returns immediately when it is `None`, judges each new record, and adds labels with `gmail-imap-label.py --add <label>` and no `--archive` flag. Read the flag as `bool((cfg.get("topic_labeling", {}) or {}).get("enabled", False))` and log the resolved state every tick, the same way `archive=DISABLED` is already logged, so "nothing is being labelled" is never a silent surprise.

- [ ] **Step 4: Run test to verify it passes**

Run (from the repo root): `python -m pytest skills/email-monitor/tests/ -q`
Expected: all tests pass, including the pre-existing suite

- [ ] **Step 5: Sweep every document**

Update, in each case describing the capability rather than the operator's private taxonomy: `SKILL.md` workflow table gains the topic labeling row; `README.md` and `README_CN.md` gain a short section; `CONFIG.md` documents `topic_labeling.enabled`, `rules/taxonomy.md`, `rules/sender_map.json`, `rules/labels.json`; `CHANGELOG.md` gains the entry; `ROADMAP.md` records the deferred items from the spec (provenance ledger, frozen regression gating, novelty gate, stratified audit).

- [ ] **Step 6: Run all three repo gates and check the real exit codes**

```bash
git add -A
python tools/pii_guard.py --staged > /dev/null 2>&1; echo "pii=$?"
python tools/dash_guard.py > /dev/null 2>&1; echo "dash=$?"
python tools/data_boundary.py > /dev/null 2>&1; echo "boundary=$?"
```

Expected: `pii=0 dash=0 boundary=0`. Check each exit code separately; a pipe into `tail` or `head` masks the real status.

- [ ] **Step 7: Commit**

```bash
git commit -m "feat: gated topic labeling in the incremental tick, and docs

Off by default; the resolved state is logged every tick so a disabled capability
is visible rather than silent. The topic write path adds labels only and cannot
reach the archive helper, asserted structurally."
```

---

## Self-Review

**Spec coverage.** Invariant 1 (one standard, private) is Task 4 plus its structural test that the repo tracks no taxonomy file. Invariant 2 (one producer) is Tasks 1 to 3, with Tasks 7 and 8 as callers that add no judgement. Invariant 3 (one writer) is Task 7's IMAP layer and Task 8's `topic_label`. Invariant 4 (never de-inbox) is asserted twice, structurally in Task 8 and in the Task 6 generator property test. Invariant 5 (omission over commission) is the three state verdict in Task 3 and the evidence gate in Task 1. Invariant 6 (inert when uninitialised) is Task 4 and the Task 8 default. The four pipeline stages map to Tasks 2, 3, 1, 3. Filter generation is Task 6; the specific rules to delete are an operational step for the live mailboxes, carried out with the operator, not a code change. Deferred items are recorded in `ROADMAP.md` in Task 8 step 5.

**Placeholder scan.** No TBD, no "add error handling", no "similar to Task N". Every code step carries runnable code. Task 7's IMAP layer is described as a port of an already validated sequence rather than pasted in full, which is the one place a reader must consult the working batch scripts; the pure functions it depends on are given in full and are what the tests cover.

**Type consistency.** `judge` returns `{"state", "labels", "dropped", "reason"}` in Task 3 and is consumed with exactly those keys in Tasks 5, 7 and 8. Label items are `{"label", "evidence", "source"}` everywhere, gaining `"drop_reason"` only when dropped. `load_config` returns `{"taxonomy", "sender_map", "allowed_labels"}` in Task 4 and is unpacked with those names in Task 8. `sender_map` keys `by_address`, `by_domain`, `by_list_id` are identical in Tasks 2, 4 and 6. `plan_changes` returns `{"add", "remove"}` in Task 7 and is consumed with those keys.
