#!/usr/bin/env python3
"""Generate public fixtures from independently invented behavior cases.

Every address, subject and runtime record must be synthetic. Keep the case table
as the source of truth; never copy operational mail or reports into it. The data
boundary check regenerates committed fixtures and compares their bytes, catching
manual changes to generated output. Reviewing the generator's inputs remains
necessary: reproducibility alone cannot establish that an input was invented.

Edit a behavior case, run this generator, and commit source plus generated output.
Use --out DIR to generate separately for verification. Output is deterministic:
no clock, randomness, environment or real mailbox contributes to the case table.
"""
import argparse
import json
import os
import sys

# Every message in the golden set belongs to this synthetic account. It is not a real mailbox, and it
# is not a slug from the operator's registry -- the registry lives in the private companion config and
# is deliberately out of this repo's reach (see tools/datadir.py).
ACCOUNT = "user1"

# ---------------------------------------------------------------------------------------------
# THE CASE TABLE -- the only hand-written thing here, and the only thing you may change.
#
# Each case pins ONE classifier behaviour: a signal goes in, a priority must come out. The `path`
# field names the branch of em_classify.py that is under test, so that a case cannot be added without
# saying what it is for -- and so a future refactor that deletes a branch has a named test to answer.
#
# HARD RULE: every value below is INVENTED. Synthetic namespace only -- `example.com`,
# `example-<role>.com`. Never a real vendor, person, employer, or address, not even one that seems
# harmless. "It was only a store's marketing address" is how the last leak was rationalized.
#
# Priorities: URGENT | ACTION | FYI | NOISE. Safe-fail is FYI (park it), never NOISE (silent swallow).
# ---------------------------------------------------------------------------------------------
CASES = [
    {
        "path": "L0 vip -> ACTION",
        "why": "a sender on the personal VIP layer is always actionable, whatever the subject says",
        "frm": "recruiter@example-employer.com",
        "subject": "Re: start date confirmation",
        "expect": "ACTION",
        "note": "VIP sender via personal layer",
    },
    {
        "path": "L0 urgent_keyword -> URGENT",
        "why": "an urgency word in the subject is high-confidence enough to skip scoring entirely",
        "frm": "billing@example-utility.com",
        "subject": "URGENT: payment failed, account will be suspended",
        "expect": "URGENT",
        "note": "urgent keyword in subject",
    },
    {
        "path": "L1 low_score+marketing -> NOISE",
        "why": "marketing label plus a dead score is the one case allowed to bypass the FYI safe-fail",
        "frm": "promo@example-shoes.com",
        "subject": "50% off sale ends tonight, shop now",
        "list_unsubscribe": True,
        "expect": "NOISE",
        "note": "marketing + list-unsubscribe + noreply pattern",
    },
    {
        "path": "L0 list_unsub+noreply -> NOISE",
        "why": "List-Unsubscribe AND a no-reply sender: bulk by construction, no human waiting",
        "frm": "no-reply@example-social.com",
        "subject": "Someone liked your photo",
        "list_unsubscribe": True,
        "expect": "NOISE",
        "note": "social noreply list-unsub",
    },
    {
        "path": "L0 vip -> ACTION (with action verb)",
        "why": "VIP fires at L0 before scoring; pins that the deadline in the subject cannot demote it",
        "frm": "leasing@example-property.com",
        "subject": "Please sign the renewal by Friday",
        "expect": "ACTION",
        "note": "VIP + action verb",
    },
    {
        # KNOWN MISS -- LEAVE IT MISSING. The classifier answers FYI here; this case demands NOISE.
        # A List-Unsubscribe newsletter from a non-noreply sender slips past the L0 bulk rule, and at
        # L1 its subject carries no marketing word ("Your weekly digest"), so it labels as
        # `notification` and safe-fails to FYI. That is the single failure in the golden set: accuracy
        # is 7/8 = 0.875 against the >= 0.85 gate in test_acceptance.py.
        #
        # This case is the standing pressure on that gap. Do NOT "fix" it by relaxing `expect` to FYI
        # to make the suite greener -- that deletes the only record that the gap exists and buys
        # exactly nothing. Fix it, if you fix it, in em_classify.py (L0 should weigh List-Unsubscribe
        # without requiring a no-reply sender), and then this case starts passing on its own.
        "path": "L1 newsletter -> NOISE (currently FYI: known classifier gap)",
        "why": "bulk newsletters should be NOISE even when the sender is not a no-reply address",
        "frm": "newsletter@example-blog.com",
        "subject": "Your weekly digest",
        "list_unsubscribe": True,
        "expect": "NOISE",
        "note": "newsletter marketing",
    },
    {
        "path": "L1 low_score_safe_fyi -> FYI",
        "why": "an unknown human with no signal must PARK (FYI), never be swallowed (NOISE)",
        "frm": "colleague@example-employer.com",
        "subject": "quick question about the deck",
        "expect": "FYI",
        "note": "unknown sender, no strong signal -> safe FYI",
    },
    {
        "path": "L1 calendar -> FYI",
        "why": "a calendar label alone is not an obligation; it parks rather than escalating",
        "frm": "calendar@example-meetings.com",
        "subject": "Meeting invite: project sync tomorrow",
        "expect": "FYI",
        "note": "calendar notification, unknown sender",
    },
]

FIXTURE = os.path.join("skills", "email-monitor", "tests", "golden_classify.jsonl")


def build_row(case):
    """One CASE -> one golden row. The msg keys are inserted in a FIXED order on purpose.

    Do not "tidy" this into json.dumps(sort_keys=True): the committed fixture is written in this
    order, and data_boundary.py compares bytes, so reordering keys would look exactly like a
    hand-edited (i.e. possibly real) fixture. Insertion order is deterministic in Python (>=3.7),
    which is all determinism requires here.
    """
    msg = {"from": case["frm"], "subject": case["subject"]}
    if case.get("list_unsubscribe"):
        msg["list_unsubscribe"] = True
    msg["account"] = ACCOUNT
    return {"msg": msg, "expect_priority": case["expect"], "note": case["note"]}


def render():
    """The whole fixture as one string. UTF-8, LF, trailing newline, no BOM."""
    return "".join(
        json.dumps(build_row(c), ensure_ascii=False, sort_keys=False) + "\n" for c in CASES)


TOPIC_FIXTURE = os.path.join("skills", "email-monitor", "tests", "topic_regression.jsonl")

# Invented cases isolate topic-evidence failure shapes without operational mail.
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


def build_topic_row(case):
    """One TOPIC_CASE -> one topic-regression row. Same fixed-key-order discipline as
    build_row: data_boundary.py compares bytes, so key order is part of the contract."""
    return {"shape": case["shape"], "from": case["from"], "subject": case["subject"],
            "expect_labels": case["expect_labels"], "why": case["why"]}


def render_topic():
    """The whole topic-regression fixture as one string. Same format rules as render()."""
    return "".join(
        json.dumps(build_topic_row(c), ensure_ascii=False, sort_keys=False) + "\n"
        for c in TOPIC_CASES)


RELIABILITY_FIXTURE = os.path.join("skills", "email-monitor", "tests", "reliability.json")
DRAFTING_FIXTURE = os.path.join("skills", "email-monitor", "tests", "drafting.json")
ALERT_FIXTURE = os.path.join("skills", "email-monitor", "tests", "alert_disposition.json")


def alert_disposition_cases():
    """Synthetic helper responses for retry and duplicate-suppression boundaries."""
    proof = {"status": "not_applied", "adapter": "alert",
             "evidence": "Synthetic relay checked its ledger; delivery did not occur."}
    delivered = {"status": "confirmed", "adapter": "alert",
                 "receipt_id": "synthetic-alert-receipt-27"}
    cases = [
        {"response": proof, "returncode": code, "disposition": "not_applied"}
        for code in (0, 27)
    ]
    cases += [
        {"response": delivered, "returncode": code,
         "disposition": "confirmed" if code == 0 else "uncertain"}
        for code in (0, 27)
    ]
    for response in (None, [], {}, {**proof, "evidence": " "},
                     {**proof, "evidence": 27}, {**proof, "adapter": "pool"},
                     {**proof, "idempotency_key": "another-synthetic-action"},
                     {**proof, "status": "unknown"}, {**delivered, "receipt_id": ""}):
        for code in (0, 27):
            cases.append({"response": response, "returncode": code, "disposition": "uncertain"})
    cases.append({"raw_stdout": "synthetic non-JSON failure", "returncode": 27,
                  "disposition": "uncertain"})
    return {"key": "synthetic-alert-action-27", "message": "Synthetic alert for review",
            "event_id": "synthetic-summary-event-27", "cases": cases}


def drafting_cases():
    """Invented identities and draft bodies for configuration and lint regressions."""
    signature = "Avery Example"
    greeting = "Hi Taylor,"
    signoff = "Thanks,\n" + signature

    def draft(body, hello="Hi,", spaced=False):
        sep = "\n\n" if spaced else "\n"
        return sep.join((hello, body, signoff))

    return {
        "config": {"signature": signature, "language": "en", "style": {}},
        "alternate_config": {"signature": "Morgan Example", "language": "en", "style": {}},
        "literal_config": {"signature": "Avery {Example}", "language": "en", "style": {}},
        "recipient": "Taylor Example", "body": "Please confirm the appointment today.",
        "greeting": greeting, "signature": signature,
        "expanded_signature": signature + ", Inc", "signoff": signoff,
        "clean_dealer": draft(
            "I am looking to buy a 2026 Acme Auto Compact and I am ready to move this week.\n\n"
            "Please send your best out-the-door price as one number: discounted selling price, "
            "minus rebates, plus all fees. I have my own financing, so quote price only.\n\n"
            "I am contacting a few dealers within 50 miles and will go with the cleanest quote. "
            "If you send a written breakdown today, I can commit fast.", greeting, spaced=True),
        "kill_list": draft("I wanted to reach out to leverage our synergy and delve into next steps.", spaced=True),
        "long_lines": draft("\n".join("This is line number %d here." % i for i in range(15)), greeting, spaced=True),
        "rule_of_three": draft("Our service is fast, reliable, and affordable.", greeting, spaced=True),
        "two_item": draft("Please send the price and the fees in one number.", greeting, spaced=True),
        "journey": draft("Let us start this journey together.", spaced=True),
        "roadmap": draft("Here is our product roadmap for the year.", spaced=True),
        "worth_noting": draft("It's worth noting that the offer expires Friday."),
        "important_to_note": draft("It is important to note that the deposit is due Monday."),
        "that_said": draft("That said, I can sign this week."),
        "clean_ask": draft("Please send your best out the door price today.", greeting),
        "noted": draft("I noted your point about the timing and agree."),
        "transition_template": draft("%s I will send the documents this week."),
        "additional": draft("I have additional questions about the timeline."),
        "therefore": draft("We can therefore proceed once you confirm."),
        "hedge_template": draft("%s"),
        "concrete_event": draft("I look forward to the test drive on Saturday."),
        "bare_ask": draft("Let me know which trim you have in stock."),
        "custom_template": "Custom text kept exactly.\n{signature}\n",
    }


def lint_closure_cases():
    """Generate required repairs, spelling variants and ordinary-prose checks."""
    drafts = drafting_cases()

    def case(name, body, diagnostic=None):
        row = {
            "id": name,
            "text": "Hi,\n" + body + "\n" + drafts["signoff"],
            "profile": "business",
        }
        if diagnostic is not None:
            row["diagnostic"] = diagnostic
        return row

    required = []
    for name, diagnostic in (
        ("rule_of_three", "rule-of-three"),
        ("journey", "AI kill-list words: journey"),
        ("roadmap", "AI kill-list words: roadmap"),
        ("worth_noting", "meta-commentary"),
        ("important_to_note", "meta-commentary"),
        ("that_said", "throat-clearing"),
    ):
        required.append({"id": name, "text": drafts[name],
                         "profile": "business", "diagnostic": diagnostic})

    variants = [
        case("hyphenated_predicate", "The design is easy-to-use, quick, and compact.", "rule-of-three"),
        case("uppercase_predicate", "The reply WAS CLEAR, DIRECT, AND BRIEF.", "rule-of-three"),
        case("wrapped_predicate", "The service will be fast,\nreliable,\nand affordable.", "rule-of-three"),
        case("question_predicate", "Can the reply be clear, direct, and brief?", "rule-of-three"),
        case("worth_mentioning_contraction", "It's worth mentioning that the quote expires Friday.", "meta-commentary"),
        case("worth_mentioning_full", "It is worth mentioning that the quote expires Friday.", "meta-commentary"),
        case("important_note_contraction", "It's important to note that the quote expires Friday.", "meta-commentary"),
        case("important_mention_full", "It is important to mention that the quote expires Friday.", "meta-commentary"),
        case("curly_contraction", "It’s worth mentioning that the quote expires Friday.", "meta-commentary"),
        case("wrapped_meta_commentary", "It\nis worth\nmentioning that the quote expires Friday.", "meta-commentary"),
        case("having_said_that", "Having said that, I can sign today.", "throat-clearing"),
        case("with_that_said", "With that said, I can sign today.", "throat-clearing"),
        case("indented_opener", "  that said, I can sign today.", "throat-clearing"),
        case("later_line_opener", "The quote is ready.\nThat said, I can sign today.", "throat-clearing"),
    ]
    ordinary = [
        {"id": "clean_dealer", "text": drafts["clean_dealer"], "profile": "dealer"},
        {"id": "two_item", "text": drafts["two_item"], "profile": "business"},
        {"id": "noted", "text": drafts["noted"], "profile": "business"},
        {"id": "concrete_event", "text": drafts["concrete_event"], "profile": "business"},
        case("two_predicates", "The reply is clear and brief."),
        case("object_list", "Please send the quote, receipt, and form."),
        case("numeric_list", "The totals are 10, 20, and 30."),
        case("noun_phrase_list", "The files are a receipt, a quote, and a form."),
        case("worth_considering", "The replacement is worth considering."),
        case("quoted_said", "The note that said, 'bring a receipt' was removed."),
        case("important_action", "It is important to vote by Friday."),
    ]
    return {"config": drafts["config"], "required": required,
            "variants": variants, "ordinary": ordinary}


def reliability_cases():
    """Invented inputs for durable dispatch, configuration and recovery tests."""
    return {
        "account": {"slug": "user1", "user": "user1@example.com"},
        "message": {"uid": 11, "gm_msgid": "1011", "message_id": "<notice-11@example.com>",
                    "thread_key": "thread-11", "from": "sender@example.com",
                    "subject": "Please review the draft", "body": "Please review it.",
                    "date": "2026-01-02", "list_unsubscribe": False},
        "cursor": {"uidvalidity": 7, "last_uid": 11},
        "verdict": {"priority": "ACTION", "label": "personal", "tier": "L0"},
        "private_remote": "https://github.com/example-owner/email-monitor-config.git",
        "visibility": {"example-owner/email-monitor-config": "PRIVATE"},
        "private_proof": {
            "now": "2030-06-15T12:00:00+00:00",
            "fresh": "2030-06-14T12:00:00+00:00",
            "stale": "2029-01-01T00:00:00+00:00",
            "future": "2031-01-01T00:00:00+00:00",
            "slug": "example-owner/email-monitor-config",
            "alias": "synthetic-github",
            "ssh_config": "Host synthetic-github\n    HostName github.com\n    User git\n",
            "unsafe_ssh_rules": ["Match exec synthetic-command", "Include synthetic-config", "CanonicalizeHostname yes"],
            "unrelated_https": "https://synthetic-github/example-owner/email-monitor-config.git",
        },
        "draft": {"signature": "Avery Example", "language": "zh",
                  "style": {"max_lines": 8, "max_sentences": 5, "allow_markdown": False}},
        "helper_contract": {"idempotency_key": "synthetic-action-11", "label": "Review",
                            "evidence": "Synthetic helper verified that no write occurred.",
                            "receipt_id": "synthetic-receipt-11",
                            "selected_python": "selected runtime/python.exe",
                            "unavailable_python": "unavailable/python.exe"},
        "draft_text": "收到。我明天回复。\n\nAvery Example",
        "model_policy": {
            "taxonomy": "Use Review only for messages explicitly requesting a review.",
            "topic_config": {"taxonomy": "Use Review only for an explicit review request.",
                             "sender_map": {}, "allowed_labels": ["Review"], "type_labels": []},
            "verdict": {"priority": "ACTION", "label": "review", "confidence": 0.8},
            "legacy_api": [{"chain": ["synthetic-route"]},
                           {"providers": {"synthetic-route": {"model": "synthetic-model"}}},
                           {"timeout": 17}],
            "legacy_cli": [["--chain", "synthetic-route"], ["--timeout", "17"],
                           ["--codex-model", "synthetic-model"], ["--codex-reasoning", "synthetic-effort"],
                           ["--claude-model", "synthetic-model"]],
            "legacy_registry": [{"classifier": {"chain": ["synthetic-route"]}},
                                {"classifier": {"providers": {}}},
                                {"classifier": {"timeout_sec": 17}},
                                {"topic_labeling": {"timeout_sec": 17}}],
        },
    }


def doctor_cases():
    """Invented config inputs for readiness checks; no mailbox or credentials."""
    ignored = "secrets/*\n!secrets/README.md\n*.env\n*.cred\n"
    return {
        "account": {"slug": "user1", "user": "user1@example.com", "role": "primary"},
        "draft": {"signature": "Avery Example", "language": "en", "style": {}},
        "sender_map": {"version": 1, "by_address": {"sender@example.com": "Review"},
                       "by_domain": {}, "by_list_id": {}},
        "labels": ["Review"],
        "profiles": ["business", "dealer", "support", "personal"],
        "template": "Hello,\n\n{body}\n\n{signature}\n",
        "git_head": "ref: refs/heads/main\n",
        "git_config": "[core]\nrepositoryformatversion = 0\nbare = false\n",
        "private_proof": {"repository": "example-owner/email-monitor-config", "proof": "synthetic"},
        "valid_slugs": ["user1", "user.one+alerts@example.com", "user-2"],
        "invalid_slugs": ["../outside", "nested/name", "nested\\name", ".", "..", "has space", 7, ["user1"],
                          "CON", "nul.txt", "lpt1"],
        "invalid_users": ["", "  ", 7, ["user1@example.com"]],
        "ignore_variants": [
            {"name": "normal", "text": ignored, "ready": True},
            {"name": "whole_directory", "text": "secrets/\n*.env\n*.cred\n", "ready": True},
            {"name": "commented", "text": "# secrets/\n# *.env\n# *.cred\n", "ready": False},
            {"name": "reincluded_env", "text": ignored + "!*.env\n", "ready": False},
            {"name": "reincluded_cred", "text": ignored + "!*.cred\n", "ready": False},
            {"name": "reincluded_secrets", "text": ignored + "!secrets/*\n", "ready": False},
        ],
    }



PARSER_TEST = "skills/email-monitor/tests/test_response_json_boundary.py"

RESPONSE_CASES_JSON = r'''{
  "malformed": [
    {
      "id": "truncated_object-urgent-plain",
      "text": "{\"example\":{\"priority\":\"URGENT\"}"
    },
    {
      "id": "truncated_object-urgent-fenced",
      "text": "```json\n{\"example\":{\"priority\":\"URGENT\"}\n```"
    },
    {
      "id": "truncated_object-urgent-prose",
      "text": "Synthetic model answer: {\"example\":{\"priority\":\"URGENT\"} End of answer."
    },
    {
      "id": "truncated_array-urgent-plain",
      "text": "[{\"priority\":\"URGENT\"}"
    },
    {
      "id": "truncated_array-urgent-fenced",
      "text": "```json\n[{\"priority\":\"URGENT\"}\n```"
    },
    {
      "id": "truncated_array-urgent-prose",
      "text": "Synthetic model answer: [{\"priority\":\"URGENT\"} End of answer."
    },
    {
      "id": "truncated_deep-urgent-plain",
      "text": "{\"examples\":[{\"priority\":\"URGENT\"}]"
    },
    {
      "id": "truncated_deep-urgent-fenced",
      "text": "```json\n{\"examples\":[{\"priority\":\"URGENT\"}]\n```"
    },
    {
      "id": "truncated_deep-urgent-prose",
      "text": "Synthetic model answer: {\"examples\":[{\"priority\":\"URGENT\"}] End of answer."
    },
    {
      "id": "invalid_outer-urgent-plain",
      "text": "{\"example\":{\"priority\":\"URGENT\"},\"priority\":}"
    },
    {
      "id": "invalid_outer-urgent-fenced",
      "text": "```json\n{\"example\":{\"priority\":\"URGENT\"},\"priority\":}\n```"
    },
    {
      "id": "invalid_outer-urgent-prose",
      "text": "Synthetic model answer: {\"example\":{\"priority\":\"URGENT\"},\"priority\":} End of answer."
    },
    {
      "id": "truncated_object-action-plain",
      "text": "{\"example\":{\"priority\":\"ACTION\"}"
    },
    {
      "id": "truncated_object-action-fenced",
      "text": "```json\n{\"example\":{\"priority\":\"ACTION\"}\n```"
    },
    {
      "id": "truncated_object-action-prose",
      "text": "Synthetic model answer: {\"example\":{\"priority\":\"ACTION\"} End of answer."
    },
    {
      "id": "truncated_array-action-plain",
      "text": "[{\"priority\":\"ACTION\"}"
    },
    {
      "id": "truncated_array-action-fenced",
      "text": "```json\n[{\"priority\":\"ACTION\"}\n```"
    },
    {
      "id": "truncated_array-action-prose",
      "text": "Synthetic model answer: [{\"priority\":\"ACTION\"} End of answer."
    },
    {
      "id": "truncated_deep-action-plain",
      "text": "{\"examples\":[{\"priority\":\"ACTION\"}]"
    },
    {
      "id": "truncated_deep-action-fenced",
      "text": "```json\n{\"examples\":[{\"priority\":\"ACTION\"}]\n```"
    },
    {
      "id": "truncated_deep-action-prose",
      "text": "Synthetic model answer: {\"examples\":[{\"priority\":\"ACTION\"}] End of answer."
    },
    {
      "id": "invalid_outer-action-plain",
      "text": "{\"example\":{\"priority\":\"ACTION\"},\"priority\":}"
    },
    {
      "id": "invalid_outer-action-fenced",
      "text": "```json\n{\"example\":{\"priority\":\"ACTION\"},\"priority\":}\n```"
    },
    {
      "id": "invalid_outer-action-prose",
      "text": "Synthetic model answer: {\"example\":{\"priority\":\"ACTION\"},\"priority\":} End of answer."
    },
    {
      "id": "truncated_object-fyi-plain",
      "text": "{\"example\":{\"priority\":\"FYI\"}"
    },
    {
      "id": "truncated_object-fyi-fenced",
      "text": "```json\n{\"example\":{\"priority\":\"FYI\"}\n```"
    },
    {
      "id": "truncated_object-fyi-prose",
      "text": "Synthetic model answer: {\"example\":{\"priority\":\"FYI\"} End of answer."
    },
    {
      "id": "truncated_array-fyi-plain",
      "text": "[{\"priority\":\"FYI\"}"
    },
    {
      "id": "truncated_array-fyi-fenced",
      "text": "```json\n[{\"priority\":\"FYI\"}\n```"
    },
    {
      "id": "truncated_array-fyi-prose",
      "text": "Synthetic model answer: [{\"priority\":\"FYI\"} End of answer."
    },
    {
      "id": "truncated_deep-fyi-plain",
      "text": "{\"examples\":[{\"priority\":\"FYI\"}]"
    },
    {
      "id": "truncated_deep-fyi-fenced",
      "text": "```json\n{\"examples\":[{\"priority\":\"FYI\"}]\n```"
    },
    {
      "id": "truncated_deep-fyi-prose",
      "text": "Synthetic model answer: {\"examples\":[{\"priority\":\"FYI\"}] End of answer."
    },
    {
      "id": "invalid_outer-fyi-plain",
      "text": "{\"example\":{\"priority\":\"FYI\"},\"priority\":}"
    },
    {
      "id": "invalid_outer-fyi-fenced",
      "text": "```json\n{\"example\":{\"priority\":\"FYI\"},\"priority\":}\n```"
    },
    {
      "id": "invalid_outer-fyi-prose",
      "text": "Synthetic model answer: {\"example\":{\"priority\":\"FYI\"},\"priority\":} End of answer."
    },
    {
      "id": "truncated_object-noise-plain",
      "text": "{\"example\":{\"priority\":\"NOISE\"}"
    },
    {
      "id": "truncated_object-noise-fenced",
      "text": "```json\n{\"example\":{\"priority\":\"NOISE\"}\n```"
    },
    {
      "id": "truncated_object-noise-prose",
      "text": "Synthetic model answer: {\"example\":{\"priority\":\"NOISE\"} End of answer."
    },
    {
      "id": "truncated_array-noise-plain",
      "text": "[{\"priority\":\"NOISE\"}"
    },
    {
      "id": "truncated_array-noise-fenced",
      "text": "```json\n[{\"priority\":\"NOISE\"}\n```"
    },
    {
      "id": "truncated_array-noise-prose",
      "text": "Synthetic model answer: [{\"priority\":\"NOISE\"} End of answer."
    },
    {
      "id": "truncated_deep-noise-plain",
      "text": "{\"examples\":[{\"priority\":\"NOISE\"}]"
    },
    {
      "id": "truncated_deep-noise-fenced",
      "text": "```json\n{\"examples\":[{\"priority\":\"NOISE\"}]\n```"
    },
    {
      "id": "truncated_deep-noise-prose",
      "text": "Synthetic model answer: {\"examples\":[{\"priority\":\"NOISE\"}] End of answer."
    },
    {
      "id": "invalid_outer-noise-plain",
      "text": "{\"example\":{\"priority\":\"NOISE\"},\"priority\":}"
    },
    {
      "id": "invalid_outer-noise-fenced",
      "text": "```json\n{\"example\":{\"priority\":\"NOISE\"},\"priority\":}\n```"
    },
    {
      "id": "invalid_outer-noise-prose",
      "text": "Synthetic model answer: {\"example\":{\"priority\":\"NOISE\"},\"priority\":} End of answer."
    }
  ],
  "valid": [
    {
      "id": "valid-outer-urgent-plain",
      "text": "{\"priority\":\"URGENT\",\"label\":\"synthetic-result\",\"reason\":\"Acme note contains a literal } brace.\",\"example\":{\"priority\":\"NOISE\"}}",
      "expected": {
        "priority": "URGENT",
        "label": "synthetic-result",
        "reason": "Acme note contains a literal } brace.",
        "example": {
          "priority": "NOISE"
        }
      }
    },
    {
      "id": "valid-outer-urgent-fenced",
      "text": "```json\n{\"priority\":\"URGENT\",\"label\":\"synthetic-result\",\"reason\":\"Acme note contains a literal } brace.\",\"example\":{\"priority\":\"NOISE\"}}\n```",
      "expected": {
        "priority": "URGENT",
        "label": "synthetic-result",
        "reason": "Acme note contains a literal } brace.",
        "example": {
          "priority": "NOISE"
        }
      }
    },
    {
      "id": "valid-outer-urgent-prose",
      "text": "Synthetic model answer: {\"priority\":\"URGENT\",\"label\":\"synthetic-result\",\"reason\":\"Acme note contains a literal } brace.\",\"example\":{\"priority\":\"NOISE\"}} End of answer.",
      "expected": {
        "priority": "URGENT",
        "label": "synthetic-result",
        "reason": "Acme note contains a literal } brace.",
        "example": {
          "priority": "NOISE"
        }
      }
    },
    {
      "id": "valid-outer-action-plain",
      "text": "{\"priority\":\"ACTION\",\"label\":\"synthetic-result\",\"reason\":\"Acme note contains a literal } brace.\",\"example\":{\"priority\":\"NOISE\"}}",
      "expected": {
        "priority": "ACTION",
        "label": "synthetic-result",
        "reason": "Acme note contains a literal } brace.",
        "example": {
          "priority": "NOISE"
        }
      }
    },
    {
      "id": "valid-outer-action-fenced",
      "text": "```json\n{\"priority\":\"ACTION\",\"label\":\"synthetic-result\",\"reason\":\"Acme note contains a literal } brace.\",\"example\":{\"priority\":\"NOISE\"}}\n```",
      "expected": {
        "priority": "ACTION",
        "label": "synthetic-result",
        "reason": "Acme note contains a literal } brace.",
        "example": {
          "priority": "NOISE"
        }
      }
    },
    {
      "id": "valid-outer-action-prose",
      "text": "Synthetic model answer: {\"priority\":\"ACTION\",\"label\":\"synthetic-result\",\"reason\":\"Acme note contains a literal } brace.\",\"example\":{\"priority\":\"NOISE\"}} End of answer.",
      "expected": {
        "priority": "ACTION",
        "label": "synthetic-result",
        "reason": "Acme note contains a literal } brace.",
        "example": {
          "priority": "NOISE"
        }
      }
    },
    {
      "id": "valid-outer-fyi-plain",
      "text": "{\"priority\":\"FYI\",\"label\":\"synthetic-result\",\"reason\":\"Acme note contains a literal } brace.\",\"example\":{\"priority\":\"NOISE\"}}",
      "expected": {
        "priority": "FYI",
        "label": "synthetic-result",
        "reason": "Acme note contains a literal } brace.",
        "example": {
          "priority": "NOISE"
        }
      }
    },
    {
      "id": "valid-outer-fyi-fenced",
      "text": "```json\n{\"priority\":\"FYI\",\"label\":\"synthetic-result\",\"reason\":\"Acme note contains a literal } brace.\",\"example\":{\"priority\":\"NOISE\"}}\n```",
      "expected": {
        "priority": "FYI",
        "label": "synthetic-result",
        "reason": "Acme note contains a literal } brace.",
        "example": {
          "priority": "NOISE"
        }
      }
    },
    {
      "id": "valid-outer-fyi-prose",
      "text": "Synthetic model answer: {\"priority\":\"FYI\",\"label\":\"synthetic-result\",\"reason\":\"Acme note contains a literal } brace.\",\"example\":{\"priority\":\"NOISE\"}} End of answer.",
      "expected": {
        "priority": "FYI",
        "label": "synthetic-result",
        "reason": "Acme note contains a literal } brace.",
        "example": {
          "priority": "NOISE"
        }
      }
    },
    {
      "id": "valid-outer-noise-plain",
      "text": "{\"priority\":\"NOISE\",\"label\":\"synthetic-result\",\"reason\":\"Acme note contains a literal } brace.\",\"example\":{\"priority\":\"URGENT\"}}",
      "expected": {
        "priority": "NOISE",
        "label": "synthetic-result",
        "reason": "Acme note contains a literal } brace.",
        "example": {
          "priority": "URGENT"
        }
      }
    },
    {
      "id": "valid-outer-noise-fenced",
      "text": "```json\n{\"priority\":\"NOISE\",\"label\":\"synthetic-result\",\"reason\":\"Acme note contains a literal } brace.\",\"example\":{\"priority\":\"URGENT\"}}\n```",
      "expected": {
        "priority": "NOISE",
        "label": "synthetic-result",
        "reason": "Acme note contains a literal } brace.",
        "example": {
          "priority": "URGENT"
        }
      }
    },
    {
      "id": "valid-outer-noise-prose",
      "text": "Synthetic model answer: {\"priority\":\"NOISE\",\"label\":\"synthetic-result\",\"reason\":\"Acme note contains a literal } brace.\",\"example\":{\"priority\":\"URGENT\"}} End of answer.",
      "expected": {
        "priority": "NOISE",
        "label": "synthetic-result",
        "reason": "Acme note contains a literal } brace.",
        "example": {
          "priority": "URGENT"
        }
      }
    },
    {
      "id": "valid-depth-plain",
      "text": "{\"priority\":\"ACTION\",\"label\":\"synthetic-depth\",\"reason\":\"Acme bounded nested example.\",\"value\":[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]}",
      "expected": {
        "priority": "ACTION",
        "label": "synthetic-depth",
        "reason": "Acme bounded nested example.",
        "value": [
          [
            [
              [
                [
                  [
                    [
                      [
                        [
                          [
                            [
                              [
                                [
                                  [
                                    [
                                      [
                                        [
                                          [
                                            [
                                              [
                                                [
                                                  [
                                                    [
                                                      [
                                                        [
                                                          [
                                                            [
                                                              [
                                                                [
                                                                  [
                                                                    [
                                                                      [
                                                                        0
                                                                      ]
                                                                    ]
                                                                  ]
                                                                ]
                                                              ]
                                                            ]
                                                          ]
                                                        ]
                                                      ]
                                                    ]
                                                  ]
                                                ]
                                              ]
                                            ]
                                          ]
                                        ]
                                      ]
                                    ]
                                  ]
                                ]
                              ]
                            ]
                          ]
                        ]
                      ]
                    ]
                  ]
                ]
              ]
            ]
          ]
        ]
      }
    },
    {
      "id": "valid-depth-prose",
      "text": "Synthetic model answer: {\"priority\":\"ACTION\",\"label\":\"synthetic-depth\",\"reason\":\"Acme bounded nested example.\",\"value\":[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]]} End of answer.",
      "expected": {
        "priority": "ACTION",
        "label": "synthetic-depth",
        "reason": "Acme bounded nested example.",
        "value": [
          [
            [
              [
                [
                  [
                    [
                      [
                        [
                          [
                            [
                              [
                                [
                                  [
                                    [
                                      [
                                        [
                                          [
                                            [
                                              [
                                                [
                                                  [
                                                    [
                                                      [
                                                        [
                                                          [
                                                            [
                                                              [
                                                                [
                                                                  [
                                                                    [
                                                                      [
                                                                        0
                                                                      ]
                                                                    ]
                                                                  ]
                                                                ]
                                                              ]
                                                            ]
                                                          ]
                                                        ]
                                                      ]
                                                    ]
                                                  ]
                                                ]
                                              ]
                                            ]
                                          ]
                                        ]
                                      ]
                                    ]
                                  ]
                                ]
                              ]
                            ]
                          ]
                        ]
                      ]
                    ]
                  ]
                ]
              ]
            ]
          ]
        ]
      }
    }
  ],
  "nonverdict": [
    {
      "id": "array-of-example",
      "text": "[{\"priority\":\"URGENT\"}]"
    },
    {
      "id": "empty-array",
      "text": "[]"
    },
    {
      "id": "plain-json-string",
      "text": "\"synthetic text\""
    },
    {
      "id": "boolean",
      "text": "true"
    }
  ],
  "limits": [
    {
      "id": "integer-limit-plain",
      "text": "{\"priority\":\"ACTION\",\"value\":99999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999}"
    },
    {
      "id": "integer-limit-prose",
      "text": "Synthetic model answer: {\"priority\":\"ACTION\",\"value\":99999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999999} End of answer."
    }
  ],
  "controlled_errors": [
    {
      "id": "loads-ValueError",
      "site": "loads",
      "error": "ValueError"
    },
    {
      "id": "loads-RecursionError",
      "site": "loads",
      "error": "RecursionError"
    },
    {
      "id": "raw_decode-ValueError",
      "site": "raw_decode",
      "error": "ValueError"
    },
    {
      "id": "raw_decode-RecursionError",
      "site": "raw_decode",
      "error": "RecursionError"
    }
  ]
}'''

PARSER_TEST_SOURCE = r'''"""Generated response-boundary cases; source of truth is tools/make_fixtures.py."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills" / "email-monitor" / "scripts"))
import em_agent_classify as classifier

_spec = importlib.util.spec_from_file_location(
    "email_response_parser_fixtures", ROOT / "tools" / "make_fixtures.py")
_generator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_generator)
CASES = _generator.response_parser_cases()


def classify_reply(monkeypatch, text):
    requests = []
    def transport(prompt, **options):
        requests.append((prompt, options))
        assert options["mode"] == "judge"
        assert options["extract"] is classifier._valid_verdict
        data = options["extract"](text)
        return None if data is None else SimpleNamespace(data=data, provider="synthetic")
    monkeypatch.setattr(classifier, "_llmcall", transport)
    result = classifier.classify({
        "from": "user1@example.com", "subject": "Synthetic review request",
        "body": "Please review the AcmeCorp example."})
    assert len(requests) == 1
    return result


@pytest.mark.parametrize("case", CASES["malformed"], ids=lambda case: case["id"])
def test_malformed_outer_never_becomes_an_example_verdict(monkeypatch, case):
    assert classifier._extract_json(case["text"]) is None
    assert classifier._valid_verdict(case["text"]) is None
    assert classify_reply(monkeypatch, case["text"]) is None


@pytest.mark.parametrize("case", CASES["valid"], ids=lambda case: case["id"])
def test_valid_outer_reply_preserves_its_actual_priority(monkeypatch, case):
    assert classifier._extract_json(case["text"]) == case["expected"]
    assert classifier._valid_verdict(case["text"]) == case["expected"]
    result = classify_reply(monkeypatch, case["text"])
    assert result["priority"] == case["expected"]["priority"]
    assert result["label"] == case["expected"]["label"]
    assert result["reason"] == case["expected"]["reason"]


@pytest.mark.parametrize("case", CASES["nonverdict"], ids=lambda case: case["id"])
def test_nonobject_json_is_not_an_accepted_verdict(monkeypatch, case):
    assert classifier._valid_verdict(case["text"]) is None
    assert classify_reply(monkeypatch, case["text"]) is None


@pytest.mark.parametrize("case", CASES["limits"], ids=lambda case: case["id"])
def test_decoder_input_limits_remain_invalid_responses(monkeypatch, case):
    assert classifier._extract_json(case["text"]) is None
    assert classifier._valid_verdict(case["text"]) is None
    assert classify_reply(monkeypatch, case["text"]) is None


@pytest.mark.parametrize("case", CASES["controlled_errors"], ids=lambda case: case["id"])
def test_decoder_input_errors_stay_within_the_extract_contract(monkeypatch, case):
    real_json = classifier.json
    error = {"ValueError": ValueError, "RecursionError": RecursionError}[case["error"]]

    def rejected_decode(*args):
        raise error("synthetic decoder input limit")

    def loads(text):
        if case["site"] == "loads":
            return rejected_decode(text)
        return real_json.loads(text)

    facade = SimpleNamespace(
        loads=loads, JSONDecoder=lambda: SimpleNamespace(raw_decode=rejected_decode))
    monkeypatch.setattr(classifier, "json", facade)
    text = '{"priority":"ACTION"}'
    if case["site"] == "raw_decode":
        text = "Synthetic model answer: " + text
    assert classifier._extract_json(text) is None
    assert classifier._valid_verdict(text) is None
    assert classify_reply(monkeypatch, text) is None
'''


def response_parser_cases():
    """Invented complete and malformed model replies, with no live model or mail input."""
    return json.loads(RESPONSE_CASES_JSON)


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write(dest, text):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    # newline="\n": never let Windows translate this to CRLF -- the bytes are the contract.
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser(description="Generate the synthetic test fixtures.")
    ap.add_argument("--out", help="write fixtures into this directory (default: regenerate in place)")
    a = ap.parse_args()

    root = a.out if a.out else repo_root()
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        dest = os.path.join(a.out, os.path.basename(FIXTURE))
        topic_dest = os.path.join(a.out, os.path.basename(TOPIC_FIXTURE))
    else:
        dest = os.path.join(root, FIXTURE)
        topic_dest = os.path.join(root, TOPIC_FIXTURE)

    _write(dest, render())
    _write(topic_dest, render_topic())
    reliability_dest = os.path.join(a.out, os.path.basename(RELIABILITY_FIXTURE)) if a.out \
        else os.path.join(root, RELIABILITY_FIXTURE)
    _write(reliability_dest, json.dumps(reliability_cases(), ensure_ascii=False, indent=2) + "\n")
    drafting_dest = os.path.join(a.out, os.path.basename(DRAFTING_FIXTURE)) if a.out \
        else os.path.join(root, DRAFTING_FIXTURE)
    _write(drafting_dest, json.dumps(drafting_cases(), ensure_ascii=False, indent=2) + "\n")
    alert_dest = os.path.join(a.out, os.path.basename(ALERT_FIXTURE)) if a.out \
        else os.path.join(root, ALERT_FIXTURE)
    _write(alert_dest, json.dumps(alert_disposition_cases(), ensure_ascii=False, indent=2) + "\n")
    parser_test_dest = os.path.join(a.out, os.path.basename(PARSER_TEST)) if a.out \
        else os.path.join(root, PARSER_TEST)
    _write(parser_test_dest, PARSER_TEST_SOURCE)
    print("make_fixtures: wrote %d case(s) -> %s" % (len(CASES), dest))
    print("make_fixtures: wrote %d case(s) -> %s" % (len(TOPIC_CASES), topic_dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
