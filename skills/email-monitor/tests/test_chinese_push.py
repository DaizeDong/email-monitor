#!/usr/bin/env python3
"""Guards for the Chinese push line (v0.1.9).

The owner reads ONE line on their phone. It used to be a redacted English keyword fragment:

    [ACTION] user1: Getting ready for your upcoming session

which reads like a harmless reminder -- while the body actually said the payment method was
incomplete and would block the next charge. Real tasks were missed for days. It is now the classifier's
own Chinese gist (the classifier already reads the full body):

    【待办】个人:订阅支付方式未填,下次扣款前要补

That is a deliberate, owner-approved (2026-07-13) relaxation of the "never egress content" rule --
so the guards below pin down exactly HOW MUCH may leave the machine:

  * a credential must NEVER ride along (code / token / tracking number / URL / email address),
  * but a date or an amount MUST survive -- stripping those is what made the old line useless,
  * Chinese must survive redaction at all (the old ASCII-only filter deleted a Chinese subject
    entirely, so every Chinese mail pushed the literal string "new mail"),
  * and this public repo must never hardcode a real mailbox name (that label is PII and comes from
    the private companion config).

Run: pytest -q
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, SCRIPTS)

import em_alert  # noqa: E402
import em_tick  # noqa: E402


# ---------- secrets must not ride along in the gist ----------

def test_verification_code_stripped_from_gist():
    out = em_alert.redact_push("验证码 A1B2C3D4 请在10分钟内输入")
    assert "A1B2C3D4" not in out and "见邮箱" in out


def test_api_token_stripped_from_gist():
    out = em_alert.redact_push("token 9Xh2Kq7Lm4Zt 即将过期")
    assert "9Xh2Kq7Lm4Zt" not in out


def test_tracking_number_stripped_from_gist():
    out = em_alert.redact_push("包裹 1Z999AA10123456784 已送达")
    assert "1Z999AA10123456784" not in out and "999" not in out


def test_url_and_email_stripped_from_gist():
    out = em_alert.redact_push("点击 https://evil.example.com/reset ,或联系 boss@company.com")
    assert "http" not in out and "@" not in out and "example" not in out


def test_long_opaque_blob_stripped():
    out = em_alert.redact_push("附件 " + "a1b2c3d4e5f6g7h8i9j0k" + " 已收到")
    assert "a1b2c3d4e5f6g7h8i9j0k" not in out


# ---------- but the useful part must survive ----------

def test_amount_and_deadline_survive():
    """Stripping these is exactly what made the old alert useless."""
    out = em_alert.redact_push("PayPal 收到 $400.00,7/14 前确认")
    assert "400" in out and "7/14" in out


def test_plain_year_and_plain_word_survive():
    out = em_alert.redact_push("ARR 2026 讨论期 7/14 AoE 截止")
    assert "2026" in out and "ARR" in out and "7/14" in out


# ---------- Chinese must survive redaction ----------

def test_chinese_subject_is_not_erased():
    """The old ASCII-only filter turned every Chinese subject into the literal 'new mail'."""
    out = em_alert.redact_subject("您已成功完善PayPal中国账户登录信息")
    assert "新邮件" not in out
    assert "账户" in out or "PayPal" in out


def test_chinese_subject_still_drops_digits():
    out = em_alert.redact_subject("您的网站在 28 天内获得 20K 次点击")
    assert "28" not in out and "20K" not in out


# ---------- frame ----------

def test_title_is_chinese_and_uses_config_label():
    t = em_alert.build_title("ACTION", "slug1", "whatever",
                             summary="雇主要你确认上月工时", account_label="工作")
    assert t == "【待办】工作:雇主要你确认上月工时"


def test_title_falls_back_to_slug_without_label():
    t = em_alert.build_title("URGENT", "slug1", "x", summary="账号被锁")
    assert t.startswith("【紧急】slug1:")


def test_title_falls_back_to_redacted_subject_without_summary():
    """Heuristic fallback path (no agent verdict): still Chinese frame, redacted subject body."""
    t = em_alert.build_title("FYI", "slug1", "Payment failed on order 12345")
    assert t.startswith("【知悉】") and "12345" not in t


def test_pool_title_is_chinese():
    assert em_tick.derive_title("ACTION", "x", "subj", "工时要确认").startswith("需回复:")
    assert em_tick.derive_title("FYI", "x", "subj", "收据").startswith("待查看:")


# ---------- the PII rule this repo must keep ----------

def test_no_real_mailbox_name_hardcoded_in_alert():
    """A human-friendly mailbox label is PII: it belongs in the private config, not this repo.
    (An earlier draft of the Chinese push hardcoded the owner's real account slugs here.)"""
    src = open(os.path.join(SCRIPTS, "em_alert.py"), encoding="utf-8").read()
    assert not re.search(r"ACCOUNT_ZH\s*=\s*\{\s*[\"']\w", src), \
        "no hardcoded slug->label map: pass account_label from registry.json instead"
