"""Exercise the IMAP observation boundary with generated protocol responses."""
import copy
import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills/email-monitor/scripts"))
import em_topic
import em_watch

spec = importlib.util.spec_from_file_location("imap_fixtures", ROOT / "tools/make_fixtures.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
CASE = fixtures.imap_observation_case()


class Mailbox:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.ranges = []
        self.logouts = 0

    def login(self, *args):
        return "OK", []

    def select(self, folder, readonly):
        assert readonly
        return "OK", []

    def status(self, *args):
        return "OK", [CASE["status"]]

    def uid(self, verb, span, attributes):
        assert verb == "FETCH" and "BODY.PEEK[]" in attributes
        self.ranges.append(span)
        return next(self.responses)

    def logout(self):
        self.logouts += 1


def observe(monkeypatch, mailbox, cursor):
    monkeypatch.setattr(em_watch.imaplib, "IMAP4_SSL", lambda *args: mailbox)
    return em_watch.run_once(CASE["account"], CASE["folder"], cursor,
                             CASE["max_batch"], app_pw=CASE["password"])


def test_expunged_uid_gaps_advance_without_skipping_a_later_message(monkeypatch):
    mailbox = Mailbox([("OK", [None])] * 4 + [("OK", [(CASE["metadata"], CASE["raw"])])])
    cursor = copy.deepcopy(CASE["cursor"])
    records = []
    for _ in CASE["ranges"]:
        batch, cursor = observe(monkeypatch, mailbox, cursor)
        records.extend(batch)
    assert mailbox.ranges == CASE["ranges"]
    assert [record["uid"] for record in records] == [1004]
    assert cursor == {"uidvalidity": 1, "last_uid": 1004}
    assert mailbox.logouts == len(CASE["ranges"])


@pytest.mark.parametrize("status", ["NO", "BAD"])
def test_fetch_failure_is_not_a_successful_empty_observation(monkeypatch, status):
    cursor = copy.deepcopy(CASE["cursor"])
    mailbox = Mailbox([(status, [None])])
    with pytest.raises(RuntimeError, match="FETCH"):
        observe(monkeypatch, mailbox, cursor)
    assert cursor == CASE["cursor"] and mailbox.logouts == 1


def test_missing_uid_cannot_silently_advance_the_checkpoint(monkeypatch):
    mailbox = Mailbox([("OK", [(b"invalid metadata", CASE["raw"])])])
    with pytest.raises(RuntimeError, match="UID"):
        observe(monkeypatch, mailbox, copy.deepcopy(CASE["cursor"]))
    assert mailbox.logouts == 1


def test_fetched_list_id_reaches_the_topic_pregate():
    record = em_watch.parse_header_fetch(CASE["raw"], 1004, None, None)
    assert record["list_id"] == CASE["list_id"]
    allowed = list(CASE["sender_map"]["by_list_id"].values())
    result = em_topic.judge(record, "", CASE["sender_map"], allowed, type_labels=[])
    assert [label["label"] for label in result["labels"]] == allowed


def test_model_evidence_still_requires_from_or_subject():
    record = em_watch.parse_header_fetch(CASE["raw"], 1004, None, None)
    allowed = list(CASE["sender_map"]["by_list_id"].values())
    result = em_topic.judge(record, "", {}, allowed, call=lambda **kw: {
        "labels": [{"label": allowed[0], "evidence": CASE["list_id"], "source": "map", "evidence_header": "list_id"}]})
    assert result["labels"] == [] and result["dropped"]
