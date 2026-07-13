#!/usr/bin/env python3
"""email-monitor daily summary worker (content side; due=signal / worker=content, decoupled).

The base `tick` only emits a trigger line and cannot carry a body (store.py limit). So email-monitor
reads `due` (read-only) to learn the summary event fired, then THIS worker assembles the plain-text
digest and ships it via the Discord relay (ARCHITECTURE §2.6, anti-patterns #10/#11). After running it
marks today's event done and re-arms tomorrow's event by local-calendar recompute (NOT naive +24h,
which drifts an hour across DST).

Digest sections (Chinese): 待处理 / 等对方回复 / 草稿已备等你点发送 / 今日新增
New tasks today / Archived today (count). No bodies, no PII beyond local titles already in the pool.

Usage:
  python em_summary.py --config <registry.json> [--db PATH] [--reminder PATH] [--now ISO] [--dry]
Stdlib only.
"""
import argparse
import datetime
import json
import os
import sys
from datetime import timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import em_pool   # noqa: E402
import em_alert  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    NY = timezone(timedelta(hours=-4))


def next_summary_utc(local_time="08:00", now=None):
    """Tomorrow's summary anchor at local_time NY, returned UTC RFC3339 (DST-correct)."""
    base = now or datetime.datetime.now(tz=NY)
    if base.tzinfo is None:
        base = base.replace(tzinfo=NY)
    base = base.astimezone(NY)
    hh, mm = (int(x) for x in local_time.split(":"))
    tomorrow = (base + timedelta(days=1)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    return tomorrow.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def assemble(reminder, db):
    """Pull open/active items from the base and bucket them into a plain-text digest."""
    res = em_pool._run(reminder, db, "list", ["--source", "email-monitor", "--active", "--limit", "200"])
    items = res.get("items", [])
    important, awaiting, drafted, newtoday = [], [], [], []
    today = datetime.datetime.now(tz=timezone.utc).date().isoformat()
    for it in items:
        ext = it.get("ext") or {}
        st = it.get("state")
        pr = it.get("priority") or 9
        # pool titles are Chinese now (em_tick.derive_title); the old ASCII-only filter here would
        # strip every one of them back down to an empty row.
        title = (it.get("title") or "").strip()
        if st == "blocked":
            awaiting.append(title)
        elif ext.get("x_email_monitor_draft_id"):
            drafted.append(title)
        elif pr <= 4:
            important.append(title)
        if (it.get("created_at") or "").startswith(today):
            newtoday.append(title)

    lines = ["📬 每日邮件汇总 (%s)" % today, ""]
    def section(name, rows):
        lines.append("%s (%d):" % (name, len(rows)))
        for r in rows[:15]:
            lines.append("  - " + r)
        if not rows:
            lines.append("  (无)")
        lines.append("")
    section("待处理", important)
    section("等对方回复", awaiting)
    section("草稿已备,等你点发送", drafted)
    section("今日新增", newtoday)
    return "\n".join(lines).rstrip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--db", default=None)
    ap.add_argument("--reminder", default=em_pool.default_reminder_path())
    ap.add_argument("--now", default=None)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    with open(a.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    local_time = cfg.get("daily_summary", {}).get("local_time", "08:00")

    digest = assemble(a.reminder, a.db)
    if a.dry:
        print(digest)
        return 0
    try:
        em_alert.send(digest)
    except Exception as e:
        print("relay failed: %s" % e, file=sys.stderr)

    # mark today's summary event done + re-arm tomorrow (explicit; v0.1 has no RRULE expansion)
    d = em_pool.due(a.reminder, a.db)
    for it in d.get("items", []):
        ext = it.get("ext") or {}
        if ext.get("x_email_monitor_kind") == "daily-summary":
            try:
                em_pool.mark_done(a.reminder, a.db, it["id"])
            except Exception:
                pass
    nxt = next_summary_utc(local_time)
    day = nxt[:10]
    em_pool._run(a.reminder, a.db, "add", [
        "--kind", "event", "--title", "每日邮件汇总",
        "--due-at", nxt, "--source", "email-monitor",
        "--idempotency-key", "email-monitor:daily-summary:%s" % day,
        "--ext", json.dumps({"x_email_monitor_kind": "daily-summary"}, ensure_ascii=False)])
    print("summary sent; next armed for %s" % nxt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
