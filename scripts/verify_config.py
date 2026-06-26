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
