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
import sys

PASS, FAIL = "PASS", "FAIL"
ENV_VAR = "EMAIL_MONITOR_CONFIG"
ROLES = {"primary", "secondary", "academic"}
ABS_MARKERS = ("C:\\", "C:/", "/home/", "/Users/", "/root/")


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
    a = ap.parse_args()

    cfg, how = discover(a.config_dir)
    print("Config doctor for skill 'email-monitor'")
    print("Discovery env var: %s (and %s_DIR)" % (ENV_VAR, ENV_VAR))
    if not cfg:
        print("  [%s] config located -> none found." % FAIL)
        print("       Set %s=<dir> or run: python scripts/init_config.py" % ENV_VAR)
        return 1
    print("  resolved via %s -> %s" % (how, cfg))
    print("-" * 60)

    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    check("config dir exists", os.path.isdir(cfg))

    reg = os.path.join(cfg, "registry.json")
    reg_ok = os.path.isfile(reg)
    check("registry.json present", reg_ok)
    data = {}
    if reg_ok:
        try:
            with open(reg, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
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
                for i, ac in enumerate(accts):
                    miss = [k for k in ("slug", "user", "role") if not ac.get(k)]
                    if miss:
                        bad.append("acct[%d] missing %s" % (i, miss))
                    elif ac.get("role") not in ROLES:
                        bad.append("acct[%d] role %r not in %s" % (i, ac.get("role"), sorted(ROLES)))
                    cp = ac.get("cred_path", "")
                    if cp and any(m in cp for m in ABS_MARKERS):
                        bad.append("acct[%d] cred_path is absolute (use ~)" % i)
                check("each account has slug/user/role(enum), cred_path portable", not bad,
                      "; ".join(bad))
            check("daily_summary present", isinstance(data.get("daily_summary"), dict))
        except Exception as e:
            check("registry.json valid JSON", False, str(e))

    check("rules/ dir present", os.path.isdir(os.path.join(cfg, "rules")))
    check("templates/ dir present", os.path.isdir(os.path.join(cfg, "templates")))

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
                   if ac.get("slug") and ac.get("slug") not in labels]
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
        txt = open(gi, "r", encoding="utf-8", errors="replace").read()
        check(".gitignore blocks secrets (secrets/* + *.env + *.cred)",
              "secrets/" in txt and "*.env" in txt and "*.cred" in txt)
        check(".gitignore blocks derived layers (merged.json + _personal_layer.json)",
              "merged.json" in txt and "_personal_layer.json" in txt)

    # self-contained check (E5): no absolute-path leakage in committed config files.
    leak = []
    for rel in ("registry.json", ".gitignore",
                os.path.join("secrets", "README.md"),
                os.path.join("rules", "classification.yaml")):
        p = os.path.join(cfg, rel)
        if os.path.isfile(p):
            t = open(p, "r", encoding="utf-8", errors="replace").read()
            if any(s in t for s in ABS_MARKERS):
                leak.append(rel)
    check("self-contained (no hardcoded absolute paths)", not leak, "leaks in %s" % leak)

    n_fail = sum(1 for _, ok, _ in results if not ok)
    for nm, ok, detail in results:
        line = "  [%s] %s" % (PASS if ok else FAIL, nm)
        if detail and not ok:
            line += "  -> %s" % detail
        print(line)
    print("-" * 60)
    if n_fail:
        print("NOT READY: %d check(s) failed. Fix the above (or re-run init_config.py)." % n_fail)
        return 1
    print("READY: config at %s conforms. Fill in real accounts + DPAPI creds to go live." % cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
