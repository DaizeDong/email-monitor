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
  python em_summary.py --config <registry.json> [--python PATH] [--db PATH] [--reminder PATH] [--now ISO] [--dry]
--python overrides the configured interpreter for every helper used by this worker.
Stdlib only.
"""
import argparse
import datetime
import copy
import hashlib
import json
import os
import sys
from datetime import timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import em_pool   # noqa: E402
import em_alert  # noqa: E402
import em_actions
import em_runtime
import em_watch

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


def assemble(reminder, db, python=None):
    """Pull open/active items from the base and bucket them into a plain-text digest."""
    res = em_pool._run(reminder, db, "list", ["--source", "email-monitor", "--active", "--limit", "200"],
                       python=python)
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
    ap.add_argument("--python", help="override runtime.python for all subprocess adapters")
    ap.add_argument("--db", default=None)
    ap.add_argument("--reminder", default=em_pool.default_reminder_path())
    ap.add_argument("--now", default=None)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    report = {"status": "failed", "delivery": "not_measured", "reconciliation": "manual"}
    possible_effect = False
    try:
        em_runtime.prove_private(a.config)
        with open(a.config, encoding="utf-8-sig") as handle:
            cfg = json.load(handle)
        companion = os.path.dirname(os.path.abspath(a.config))
        runtime = em_runtime.runtime_config(cfg, companion)
        if a.python:
            runtime['python'] = em_runtime.resolve_path(a.python, companion)
        storage, _ = em_runtime.storage_config(cfg, companion, db=a.db)
        if a.dry:
            report.update(status="planned", planned_actions=["assemble", "alert", "mark_done", "arm_next"])
            print(json.dumps(report, ensure_ascii=False))
            return 0
        state_path = os.path.join(storage["state_dir"], "summary.state.json")
        state = copy.deepcopy(em_watch.load_state(state_path))
        if not isinstance(state, dict) or any(k.startswith("pending") and v for k, v in state.items()):
            raise ValueError("malformed or legacy pending summary state; migration required")
        runs = state.setdefault("summary_runs", {})
        if not isinstance(runs, dict):
            raise ValueError("summary_runs must be an object")
        for run in runs.values():
            if not isinstance(run, dict) or not isinstance(run.get("steps"), list):
                raise ValueError("malformed summary steps")
            for step in run["steps"]:
                if not isinstance(step, dict) or not {"key", "adapter", "status", "payload", "receipt"}.issubset(step):
                    raise ValueError("malformed summary step")
                if step["status"] not in em_actions.STATES:
                    raise ValueError("invalid summary step status")
                if step["status"] == "completed" and em_actions.receipt_status(step["receipt"], step["key"], step["adapter"]) != "confirmed":
                    raise ValueError("summary completion is missing its receipt")
        due = em_pool.due(a.reminder, storage["db"], python=runtime['python'])
        for item in due.get("items", []):
            if (item.get("ext") or {}).get("x_email_monitor_kind") != "daily-summary":
                continue
            run_key = hashlib.sha256(str(item["id"]).encode()).hexdigest()
            if run_key in runs:
                continue
            next_at = next_summary_utc(cfg.get("daily_summary", {}).get("local_time", "08:00"))
            plans = [("alert", {"message": assemble(a.reminder, storage["db"], python=runtime['python'])}),
                     ("summary_mark_done", {"item_id": item["id"]}),
                     ("summary_arm_next", {"due_at": next_at})]
            runs[run_key] = {"scheduled_at": item.get("due_at") or item.get("created_at"),
                             "steps": [{"key": run_key + ":" + adapter, "adapter": adapter,
                                        "status": "pending", "payload": payload, "receipt": None}
                                       for adapter, payload in plans]}
        em_watch.save_state(state_path, state)
        for run in runs.values():
            for step in run["steps"]:
                if step["status"] == "completed":
                    continue
                if step["status"] == "uncertain":
                    break
                step.update(status="uncertain", receipt=None)
                em_watch.save_state(state_path, state)
                possible_effect = True
                adapter, key, payload = step["adapter"], step["key"], step["payload"]
                receipt = None
                try:
                    if adapter == "alert":
                        receipt = em_alert.send(payload["message"], idempotency_key=key, python=runtime["python"])
                    elif adapter == "summary_mark_done":
                        confirmed = em_pool.mark_done(a.reminder, storage["db"], payload["item_id"],
                                                      python=runtime['python'])
                        if isinstance(confirmed, dict) and confirmed.get("id") == payload["item_id"] and confirmed.get("state") == "done":
                            receipt = {"status": "confirmed", "idempotency_key": key,
                                       "adapter": adapter, "receipt_id": str(confirmed["id"])}
                    elif adapter == "summary_arm_next":
                        response = em_pool._run(a.reminder, storage["db"], "add", [
                            "--kind", "event", "--title", "每日邮件汇总", "--due-at", payload["due_at"],
                            "--source", "email-monitor", "--idempotency-key", key,
                            "--ext", json.dumps({"x_email_monitor_kind": "daily-summary"})],
                            python=runtime['python'])
                        item = response.get("item", {})
                        if item.get("id") and item.get("idempotency_key") == key and item.get("due_at") == payload["due_at"]:
                            receipt = {"status": "confirmed", "idempotency_key": key,
                                       "adapter": adapter, "receipt_id": str(item["id"])}
                    else:
                        raise ValueError("unknown summary action")
                except Exception:
                    receipt = None
                disposition = em_actions.receipt_status(receipt, key, adapter)
                if disposition == "confirmed":
                    step.update(status="completed", receipt=copy.deepcopy(receipt))
                elif disposition == "not_applied":
                    step.update(status="failed", receipt=copy.deepcopy(receipt))
                em_watch.save_state(state_path, state)
                if step["status"] != "completed":
                    break
        pending = sum(step["status"] != "completed" for run in runs.values() for step in run["steps"])
        report.update(status="incomplete" if pending else "completed", pending=pending)
    except Exception as error:
        report.update(status="incomplete" if possible_effect else "failed", error=str(error))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
