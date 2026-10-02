#!/usr/bin/env python3
"""Doctor for the email-monitor companion config (config-spec E3). Resolves the config dir via the
documented discovery order, validates it against email-monitor's actual schema, and prints
PASS/FAIL per check naming exactly what is missing. Exit 0 = ready, 1 = not ready, 2 = usage error.

Discovery order (config-spec E2):
  1. $EMAIL_MONITOR_CONFIG   2. $EMAIL_MONITOR_CONFIG_DIR
  3. ~/.email-monitor-config/   4. ~/.config/email-monitor-config/

Usage:
  python verify_config.py [--config-dir <dir>]
Stdlib only. Never echoes secret values (only presence / structure).
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "email-monitor" / "scripts"))
import em_runtime
from em_lint_rules import LINE_CAPS, draft_config

PASS, FAIL = "PASS", "FAIL"
ENV_VAR = "EMAIL_MONITOR_CONFIG"
ROLES = {"primary", "secondary", "academic"}


def secret_ignore_ready(cfg):
    """Check effective repository ignore rules without creating secret files."""
    probes = ("secrets/__email_monitor_doctor_probe__", "__email_monitor_doctor_probe__.env",
              "__email_monitor_doctor_probe__.cred")
    try:
        result = subprocess.run(
            ["git", "-C", cfg, "-c", "core.excludesFile=" + os.devnull,
             "check-ignore", "--no-index", "--verbose", "-z", "--stdin"],
            input="\0".join(probes) + "\0", capture_output=True, text=True,
            encoding="utf-8", timeout=10, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
        if result.returncode != 0 or not result.stdout.endswith("\0"):
            return False
        fields = result.stdout[:-1].split("\0")
        if len(fields) != 4 * len(probes):
            return False
        matched = set()
        root = Path(cfg).resolve()
        for source, line, pattern, path in zip(*[iter(fields)] * 4):
            rule_file = (root / source).resolve()
            if (not rule_file.is_relative_to(root) or rule_file.name != ".gitignore"
                    or not line.isdigit() or not pattern or pattern.startswith("!")):
                return False
            matched.add(path)
        return matched == set(probes)
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return False


def discover(override):
    if override:
        return os.path.abspath(os.path.expanduser(override)), "explicit (--config-dir)"
    for v in (ENV_VAR, ENV_VAR + "_DIR"):
        val = os.environ.get(v)
        if val:
            return os.path.abspath(os.path.expanduser(val)), "env:%s" % v
    for d in (os.path.expanduser("~/.email-monitor-config"),
              os.path.expanduser("~/.config/email-monitor-config")):
        if os.path.isdir(d):
            return d, "default:%s" % d
    return None, None


def main():
    ap = argparse.ArgumentParser(description="Validate the email-monitor companion config.")
    ap.add_argument("--config-dir", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--python", help="explicit selected interpreter override")
    a = ap.parse_args()

    cfg, how = discover(a.config_dir)
    results = []
    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))
    if not cfg:
        cfg = ""
    check("config located", bool(cfg), "set EMAIL_MONITOR_CONFIG_DIR or pass --config-dir")

    check("config dir exists", os.path.isdir(cfg))

    reg = os.path.join(cfg, "registry.json")
    reg_ok = os.path.isfile(reg)
    check("registry.json present", reg_ok)
    data = {}
    if reg_ok:
        try:
            with open(reg, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
                raise ValueError("registry.json must contain an object")
            check("registry.json valid JSON", True)
            check("schema_version == 1", data.get("schema_version") == 1,
                  "got %r" % data.get("schema_version"))
            check("mode == 'B'", data.get("mode") == "B", "got %r" % data.get("mode"))
            accts = data.get("accounts")
            ok_list = isinstance(accts, list) and len(accts) > 0
            check("accounts[] non-empty list", ok_list,
                  "type %s" % type(accts).__name__)
            if ok_list:
                bad = []
                slugs = set()
                for i, ac in enumerate(accts):
                    if not isinstance(ac, dict):
                        bad.append("acct[%d] must be an object" % i)
                        continue
                    slug = ac.get("slug")
                    if not em_runtime.valid_account_slug(slug):
                        bad.append("acct[%d] has an invalid account slug" % i)
                    elif slug.casefold() in slugs:
                        bad.append("acct[%d] duplicates an account state filename" % i)
                    else:
                        slugs.add(slug.casefold())
                    if not isinstance(ac.get("user"), str) or not ac["user"].strip():
                        bad.append("acct[%d] user must be a nonempty string" % i)
                    if not isinstance(ac.get("role"), str) or ac["role"] not in ROLES:
                        bad.append("acct[%d] role must be one of %s" % (i, sorted(ROLES)))
                check("each account has slug/user/role(enum)", not bad,
                      "; ".join(bad))
            check("daily_summary present", isinstance(data.get("daily_summary"), dict))
        except Exception as e:
            check("registry.json valid JSON", False, str(e))

    check("rules/ dir present", os.path.isdir(os.path.join(cfg, "rules")))
    check("templates/ dir present", os.path.isdir(os.path.join(cfg, "templates")))
    for profile in LINE_CAPS:
        try:
            ready = bool((Path(cfg) / "templates" / (profile + ".txt")).read_text(
                encoding="utf-8-sig").strip())
        except (OSError, UnicodeError):
            ready = False
        check("templates/%s.txt readable and nonempty" % profile, ready)

    # rules/ contents. Until 2026-09-15 this script checked that rules/ EXISTS and never
    # looked inside: emptying rules/sender_map.json or rules/labels.json still printed
    # READY, and those two files are the whole classification behaviour of this skill.
    # Found by a config mutation probe that breaks each field and demands the doctor go red.
    rules_dir = os.path.join(cfg, "rules")

    def _load_rule(name):
        p = os.path.join(rules_dir, name)
        if not os.path.isfile(p):
            check("rules/%s present" % name, False, p)
            return None
        check("rules/%s present" % name, True)
        try:
            with open(p, "r", encoding="utf-8-sig") as f:
                doc = json.load(f)
        except Exception as e:
            check("rules/%s valid JSON" % name, False, str(e))
            return None
        check("rules/%s valid JSON" % name, True)
        if not isinstance(doc, dict):
            check("rules/%s is an object" % name, False,
                  "top level is %s" % type(doc).__name__)
            return None
        return doc

    smap = _load_rule("sender_map.json")
    if smap is not None:
        # An empty mapping is a valid JSON object, so "valid JSON" alone passes on a file
        # that classifies nothing. Every bucket must exist and at least one must be populated.
        buckets = ("by_address", "by_domain", "by_list_id")
        for b in buckets:
            check("sender_map.%s is an object" % b, isinstance(smap.get(b), dict),
                  "got %s" % type(smap.get(b)).__name__)
        populated = sum(len(smap.get(b) or {}) for b in buckets
                        if isinstance(smap.get(b), dict))
        check("sender_map has at least one rule across the three buckets", populated > 0,
              "%d total" % populated)
        check("sender_map.version present", smap.get("version") is not None)

    labels = _load_rule("labels.json")
    if labels is not None and isinstance(data.get("accounts"), list):
        # Cross-check instead of a hand-written key list: every account registered in
        # registry.json must have a labels entry. A hand-written list can be incomplete,
        # and those slugs are real account names that must never be committed here.
        missing = [ac.get("slug") for ac in data["accounts"]
                   if isinstance(ac, dict) and em_runtime.valid_account_slug(ac.get("slug"))
                   and ac["slug"] not in labels]
        check("every registered account has a labels.json entry", not missing,
              "%d account(s) registered but unmapped" % len(missing))
        mapped = [k for k in labels if not k.startswith("_")]
        check("labels.json has at least one account mapping", len(mapped) > 0)
        # Each account entry is a LIST of label names, not an object. The first draft of
        # this check asserted dict and went red on real data -- the data is the fact, so
        # the assertion moved, not the file.
        badshape = [k for k in mapped
                    if not (isinstance(labels[k], list) and labels[k]
                            and all(isinstance(x, str) for x in labels[k]))]
        check("each labels.json account entry is a non-empty list of label names",
              not badshape, "%d entr(ies) have the wrong shape" % len(badshape))

    sec = os.path.join(cfg, "secrets")
    check("secrets/ dir present", os.path.isdir(sec))

    gi = os.path.join(cfg, ".gitignore")
    gi_ok = os.path.isfile(gi)
    check(".gitignore present", gi_ok)
    if gi_ok:
        check(".gitignore blocks secret path probes (secrets/* + *.env + *.cred)",
              secret_ignore_ready(cfg), "requires Git and effective, non-negated .gitignore rules")
        # Nonsecret DATA is versioned in the PRIVATE companion. The enclosing
        # repository and every runtime destination receive visibility checks below.

    try:
        selected = em_runtime.runtime_config(data, cfg)
        if a.python:
            selected["python"] = em_runtime.resolve_path(a.python, cfg)
        em_runtime.check_local_route(selected, data.get("classifier", {}),
                                    bool(data.get("topic_labeling", {}).get("enabled", False)))
        check("local-only route capability", True)
        ready, detail = em_runtime.probe_interpreter(selected["python"])
        check("selected interpreter runtime and llmcall import", ready, detail)
    except Exception as error:
        check("runtime configuration", False, str(error))
    try:
        draft_config(data.get("draft"))
        check("draft signature, language and style", True)
    except ValueError as error:
        check("draft signature, language and style", False, str(error))
    try:
        proof = em_runtime.prove_private(cfg)
        check("config PRIVATE companion", True, proof["repository"] + ' (' + proof['proof'] + ')')
        _, proofs = em_runtime.storage_config(data, cfg)
        for name, proof in proofs.items():
            check("PRIVATE DATA destination " + name, True, proof["repository"] + ' (' + proof['proof'] + ')')
    except Exception as error:
        check("PRIVATE DATA destinations", False, str(error))

    n_fail = sum(1 for _, ok, _ in results if not ok)
    report = {"status": "not_ready" if n_fail else "ready",
              "checks": [{"name": name, "ok": ok, "detail": detail} for name, ok, detail in results]}
    summary = data.get("daily_summary")
    report["daily_summary"] = {"enabled": bool(summary.get("enabled", False)) if isinstance(summary, dict) else False,
                               "delivery": "not_measured", "reconciliation": "manual"}
    if a.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        for name, ok, detail in results:
            print("[%s] %s%s" % (PASS if ok else FAIL, name, " -> " + detail if detail else ""))
        print("NOT READY" if n_fail else "READY: configuration and runtime probes passed")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
