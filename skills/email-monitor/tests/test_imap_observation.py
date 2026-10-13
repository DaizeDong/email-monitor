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


def test_missing_uid_cannot_silently_advance_the_checkpoint(monkeypatch):
    mailbox = Mailbox([("OK", [(b"invalid metadata", CASE["raw"])])])
    with pytest.raises(RuntimeError, match="UID"):
        observe(monkeypatch, mailbox, copy.deepcopy(CASE["cursor"]))
    assert mailbox.logouts == 1
