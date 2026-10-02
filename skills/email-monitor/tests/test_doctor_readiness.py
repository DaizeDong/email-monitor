"""Doctor readiness against generated configs and real, read-only Git matching."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills/email-monitor/scripts"))
import verify_config

spec = importlib.util.spec_from_file_location("doctor_fixture_generator", ROOT / "tools/make_fixtures.py")
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)
CASES = generator.doctor_cases()


class DoctorReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="email-doctor-")
        self.addCleanup(self.temp.cleanup)
        self.cfg = Path(self.temp.name)
        for name in ("rules", "templates", "secrets", ".git/objects", ".git/refs/heads"):
            (self.cfg / name).mkdir(parents=True, exist_ok=True)
        (self.cfg / ".git/HEAD").write_text(CASES["git_head"], encoding="utf-8")
        (self.cfg / ".git/config").write_text(CASES["git_config"], encoding="utf-8")
        self.registry = {"schema_version": 1, "mode": "B", "accounts": [copy.deepcopy(CASES["account"])],
                         "daily_summary": {}, "runtime": {"python": sys.executable, "local_only": True},
                         "classifier": {"mode": "heuristic"}, "draft": copy.deepcopy(CASES["draft"])}
        self.write_json("rules/sender_map.json", CASES["sender_map"])
        self.write_json("rules/labels.json", {CASES["account"]["slug"]: CASES["labels"]})
        (self.cfg / ".gitignore").write_text(CASES["ignore_variants"][0]["text"], encoding="utf-8")
        for name in CASES["profiles"]:
            (self.cfg / "templates" / (name + ".txt")).write_text(CASES["template"], encoding="utf-8")

    def write_json(self, name, value):
        (self.cfg / name).write_text(json.dumps(value), encoding="utf-8")

    def run_doctor(self):
        self.write_json("registry.json", self.registry)
        output = io.StringIO()
        proof = CASES["private_proof"]
        with mock.patch.object(sys, "argv", ["verify_config.py", "--config-dir", str(self.cfg), "--json"]), \
             mock.patch.object(verify_config.em_runtime, "probe_interpreter", return_value=(True, "synthetic runtime")), \
             mock.patch.object(verify_config.em_runtime, "prove_private", return_value=proof), \
             mock.patch.object(verify_config.em_runtime, "storage_config", return_value=({}, {})), \
             contextlib.redirect_stdout(output):
            code = verify_config.main()
        return code, json.loads(output.getvalue())

    def test_valid_generated_config_is_ready(self):
        self.assertEqual(self.run_doctor()[0], 0)

    def test_effective_ignore_rules(self):
        for case in CASES["ignore_variants"]:
            with self.subTest(case=case["name"]):
                (self.cfg / ".gitignore").write_text(case["text"], encoding="utf-8")
                code, report = self.run_doctor()
                self.assertEqual(code, 0 if case["ready"] else 1, report)

    def test_invalid_account_slugs_are_not_ready(self):
        for slug in CASES["invalid_slugs"]:
            with self.subTest(slug=slug):
                self.registry["accounts"][0]["slug"] = slug
                if isinstance(slug, str):
                    self.write_json("rules/labels.json", {slug: CASES["labels"]})
                code, report = self.run_doctor()
                self.assertEqual(code, 1, report)
                self.assertEqual(report["status"], "not_ready")

    def test_valid_account_slugs_remain_ready(self):
        for slug in CASES["valid_slugs"]:
            with self.subTest(slug=slug):
                self.registry["accounts"][0]["slug"] = slug
                self.write_json("rules/labels.json", {slug: CASES["labels"]})
                self.assertEqual(self.run_doctor()[0], 0)

    def test_invalid_users_are_not_ready(self):
        for user in CASES["invalid_users"]:
            with self.subTest(user=user):
                self.registry["accounts"][0]["user"] = user
                self.assertEqual(self.run_doctor()[0], 1)

    def test_git_unavailable_is_not_ready(self):
        with mock.patch.object(verify_config.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(self.run_doctor()[0], 1)

    def test_each_required_template_is_checked(self):
        for name in CASES["profiles"]:
            path = self.cfg / "templates" / (name + ".txt")
            with self.subTest(profile=name):
                path.unlink()
                self.assertEqual(self.run_doctor()[0], 1)
                path.write_text(" \n", encoding="utf-8")
                self.assertEqual(self.run_doctor()[0], 1)
                path.write_bytes(b"\xff")
                self.assertEqual(self.run_doctor()[0], 1)
                path.write_text(CASES["template"], encoding="utf-8")
                self.assertEqual(self.run_doctor()[0], 0)

    def test_duplicate_account_state_names_are_not_ready(self):
        duplicate = copy.deepcopy(self.registry["accounts"][0])
        duplicate["slug"] = duplicate["slug"].upper()
        self.registry["accounts"].append(duplicate)
        self.write_json("rules/labels.json", {account["slug"]: CASES["labels"] for account in self.registry["accounts"]})
        self.assertEqual(self.run_doctor()[0], 1)


if __name__ == "__main__":
    unittest.main()
