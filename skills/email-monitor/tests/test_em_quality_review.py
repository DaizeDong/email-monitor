#!/usr/bin/env python3
"""Tests for the monthly adversarial label review.

Most of these assert a REFUSAL or a distinction, because the ways this tool can
be worse than useless all look like success: running while disabled, reporting a
clean bill of health during an outage, sampling only the newest mail, or writing
to the mailbox. Each test names the failure it catches.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import em_quality_review as qr  # noqa: E402


# ---------- the switch ----------

def test_disabled_by_default_when_the_key_is_absent(tmp_path):
    # An uninitialised machine must stay inert rather than start reviewing mail.
    p = tmp_path / "registry.json"
    p.write_text('{"accounts": []}', encoding="utf-8")
    assert qr.load_flag(str(p)) == (False, "")


def test_a_missing_registry_is_disabled_not_a_crash(tmp_path):
    assert qr.load_flag(str(tmp_path / "nope.json"))[0] is False


def test_the_flag_is_honoured(tmp_path):
    p = tmp_path / "registry.json"
    p.write_text('{"quality_review": {"enabled": true, "_note": "on"}}', encoding="utf-8")
    assert qr.load_flag(str(p)) == (True, "on")


def test_disabled_run_says_so_and_reviews_nothing(tmp_path, capsys):
    # A silent exit 0 would be indistinguishable from "reviewed everything, all clean".
    p = tmp_path / "registry.json"
    p.write_text('{"quality_review": {"enabled": false}}', encoding="utf-8")
    rc = qr.main(["--account", "acct1", "--user", "user1@example.com", "--registry", str(p)])
    assert rc == 0
    assert "DISABLED" in capsys.readouterr().out


# ---------- sampling ----------

def test_sample_spreads_across_the_corpus():
    # Taking the newest N hides exactly the old drift this review exists to find.
    items = list(range(100))
    got = qr.sample(items, 10)
    assert len(got) == 10
    assert max(got) > 50, "sample must reach the old end of the corpus, not just the newest"
    assert len(set(got)) == 10, "no duplicates"


def test_sample_is_deterministic():
    # Two runs over an unchanged corpus must produce the same report, or a diff
    # between months means nothing.
    items = list(range(100))
    assert qr.sample(items, 10) == qr.sample(items, 10)


def test_sample_returns_everything_when_the_corpus_is_small():
    assert qr.sample([1, 2, 3], 10) == [1, 2, 3]
    assert qr.sample([], 10) == []
    assert qr.sample([1, 2, 3], 0) == []


# ---------- reviewer contract ----------

def test_an_unreachable_reviewer_returns_none_not_empty():
    # [] means "reviewed, found nothing". None means "never reviewed". Collapsing
    # them would report an outage as a clean bill of health.
    assert qr.judge([("a@x", "s", "L")], "std", call=lambda p: None) is None


def test_empty_batch_is_not_an_outage():
    assert qr.judge([], "std") == []


def test_prompt_carries_the_standard_and_the_refute_instruction():
    seen = {}

    def call(prompt):
        seen["p"] = prompt
        return []

    qr.judge([("a@x.example", "Subj", "Accounts")], "THE-TAXONOMY-TEXT", call=call)
    p = seen["p"]
    assert "THE-TAXONOMY-TEXT" in p, "the reviewer must judge against the operator's standard"
    assert "REFUTE" in p, "the reviewer must default to refuting, not to finding fault"
    assert "marketing" in p.lower(), "the rule-4 marketing protection must be stated"
    assert "a@x.example" in p and "Subj" in p and "Accounts" in p


def test_reviewer_sees_only_from_and_subject():
    """It must judge on what the labeller saw.

    Asserted structurally, on the per-message block, rather than by scanning the
    whole prompt for the word "body": the instructions deliberately MENTION the
    body, to tell the reviewer it does not get one and must not speculate about it.
    A keyword scan would fail on the very sentence that enforces this.
    """
    seen = {}
    qr.judge([("a@x.example", "Subj", "Accounts")], "std",
             call=lambda p: seen.setdefault("p", p) and [])
    import re as _re
    block = seen["p"].split("MESSAGES, each with the label it currently carries:")[1]
    block = block.split("Return one entry per")[0]
    fields = set(_re.findall(r"^\s*(?:\d+\.\s*)?([A-Z]+):", block, _re.M))
    assert fields == {"FROM", "SUBJECT", "LABEL"}, \
        "only the two headers the kernel judged on, plus the label; got %r" % fields


# ---------- read-only ----------

def test_fetch_never_writes():
    # The tool is invoked in --dry mode. Today's session settled why auto-remediation
    # is wrong here: the findings usually come from a bad sender map, and stripping
    # instances without fixing the map lets the next backfill recreate them.
    seen = {}

    def runner(args, env):
        seen["args"] = args
        class P:
            returncode = 0
            stdout = "matched 1 messages\n a@x.example | Subject here"
        return P()

    qr.fetch_labelled("u@x", "Accounts", 0, None, runner=runner)
    assert "--dry" in seen["args"]
    assert "--remove" not in seen["args"]
    assert "--archive" not in seen["args"]


def test_fetch_parses_from_and_subject_and_skips_the_count_line():
    def runner(args, env):
        class P:
            returncode = 0
            stdout = ("matched 2 messages for query: x\n"
                      " a@x.example                | First subject\n"
                      " b@y.example                | Second | with pipe")
        return P()

    got = qr.fetch_labelled("u@x", "L", 0, None, runner=runner)
    assert got[0] == ("a@x.example", "First subject")
    # A subject containing a pipe must not be truncated at it.
    assert got[1] == ("b@y.example", "Second | with pipe")


def test_fetch_returns_empty_on_tool_failure():
    def runner(args, env):
        class P:
            returncode = 1
            stdout = ""
        return P()

    assert qr.fetch_labelled("u@x", "L", 0, None, runner=runner) == []


# ---------- the two reviewer errors the first real run produced ----------

def test_prompt_lists_only_this_accounts_labels():
    """A proposal outside the account's set is worse than no proposal.

    `em_topic.judge` DROPS a mapped label that is not in the account's allowed
    set, so acting on such a finding strips the message and adds nothing. Two of
    the three reviewer errors in the first real run were this same mistake.
    """
    seen = {}
    qr.judge([("a@x.example", "S", "Accounts")], "std",
             allowed=["Accounts", "Accounts/AI", "Life/Housing"],
             call=lambda p: seen.setdefault("p", p) and [])
    p = seen["p"]
    assert "Accounts/AI" in p and "Life/Housing" in p
    assert "THIS ACCOUNT HAS" in p, "the constraint must be stated, not just the list"
    # Negative control: a label the account does not have must be absent, or the
    # constraint is decorative and the reviewer can still propose it.
    assert "Accounts/Bank" not in p


def test_allowed_block_is_omitted_when_unknown():
    # An empty list must not render an empty "the account has exactly these:" section,
    # which would read as "this account has no labels" and forbid every proposal.
    seen = {}
    qr.judge([("a@x.example", "S", "Accounts")], "std", allowed=None,
             call=lambda p: seen.setdefault("p", p) and [])
    assert "THIS ACCOUNT HAS" not in seen["p"]


def test_reviewer_is_told_it_cannot_judge_body_evidence():
    """The header-only cut has to forbid BOTH directions of inference.

    The prompt already said a label survives even if the body might contradict it.
    The inverse cost a real false finding: a card notification whose subject is
    just the merchant name was called a bad receipt label because no amount
    appeared in the subject, when the amount was in the body the reviewer never
    sees. Absence from the headers is not absence from the message.
    """
    seen = {}
    qr.judge([("a@x.example", "S", "Life/Receipt")], "std",
             call=lambda p: seen.setdefault("p", p) and [])
    p = seen["p"]
    assert "BODY" in p
    low = p.lower()
    assert "absence" in low and "subject line is not evidence" in low


def test_fetch_excludes_the_owners_own_sent_mail():
    """The kernel never labels outgoing mail, so the review must not audit it.

    em_tick advances an INBOX cursor. Labels on sent mail came from thread-level
    operations during the historical retriage, and reviewing them makes the
    reviewer judge a decision the kernel never made. In one real run that was 7
    of 17 findings, crowding out the ones that could be acted on.
    """
    seen = {}

    def runner(args, env):
        seen["args"] = args
        class P:
            returncode = 0
            stdout = "matched 0 messages"
        return P()

    qr.fetch_labelled("me@example.com", "Accounts", 0, None, runner=runner)
    q = seen["args"][seen["args"].index("--query") + 1]
    assert "-from:me@example.com" in q, \
        "the query must exclude the account's own address; got %r" % q
    assert 'label:"Accounts"' in q, "and must still scope to the label"
