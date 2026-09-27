"""Offline regression tests for account-neutral payloads and portable runners.

All account IDs and workout data here are synthetic; no live API calls are made.
"""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tp
import run_tp_sync_fit_cron


class TestAccountNeutralCreate(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(tp, "get_athlete_id", return_value=12345))
        self.post = self.enterContext(patch.object(tp, "api_post"))
        self.enterContext(patch.object(
            tp, "_http_request", side_effect=AssertionError("No network in unit tests")
        ))

    def dry_run_payload(self, *options):
        args = tp.build_parser().parse_args([
            "create", "--date", "2030-01-01", "--title", "Synthetic example",
            "--dry-run", *options,
        ])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            tp.cmd_create(args)
        self.post.assert_not_called()
        return json.loads(output.getvalue().split("Dry-run POST payload:\n", 1)[1])

    def test_create_uses_resolved_athlete_and_no_invented_load(self):
        payload = self.dry_run_payload("--duration", "1")
        self.assertEqual(payload["athleteId"], 12345)
        for key in ("tssPlanned", "ifPlanned", "energyPlanned"):
            self.assertIsNone(payload[key])

    def test_explicit_load_does_not_imply_a_fixed_ftp_for_any_sport(self):
        for sport in tp._SPORT_IDS:
            with self.subTest(sport=sport):
                payload = self.dry_run_payload(
                    "--duration", "1", "--sport", sport,
                    "--tss", "50", "--if", "0.7",
                )
                self.assertEqual(payload["tssPlanned"], 50)
                self.assertEqual(payload["ifPlanned"], 0.7)
                self.assertIsNone(payload["energyPlanned"])

    def test_verified_type_id_can_override_sport(self):
        payload = self.dry_run_payload(
            "--duration", "1", "--sport", "bike", "--type-id", "42"
        )
        self.assertEqual(payload["workoutTypeValueId"], 42)

    def test_auto_tss_still_works_without_an_absolute_ftp(self):
        with tempfile.TemporaryDirectory(prefix="tp-example-") as directory:
            source = Path(directory) / "intervals.yaml"
            source.write_text(
                "steps:\n  - {type: active, duration: 1h, ftp: 100}\n",
                encoding="utf-8",
            )
            payload = self.dry_run_payload("--from-yaml", str(source), "--auto-tss")
        self.assertEqual(payload["ifPlanned"], 1.0)
        self.assertEqual(payload["tssPlanned"], 100.0)
        self.assertEqual(payload["totalTimePlanned"], 1.0)
        self.assertIsNone(payload["energyPlanned"])

    def test_no_personal_nutrition_command(self):
        self.assertNotIn("nutrition-sync", tp.build_parser().format_help())
        self.assertFalse(hasattr(tp, "cmd_nutrition_sync"))


class TestPortableRunners(unittest.TestCase):
    def test_runner_forwards_arguments_and_exit_status(self):
        argv = ["runner", "--days", "5", "--output-dir", "path with spaces", "--once"]
        with patch.object(sys, "argv", argv), patch.object(
            run_tp_sync_fit_cron.subprocess, "run"
        ) as run:
            run.return_value.returncode = 3
            self.assertEqual(run_tp_sync_fit_cron.main(), 3)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], [
            sys.executable,
            str(Path(run_tp_sync_fit_cron.__file__).resolve().with_name("tp.py")),
            "sync-fit",
        ])
        self.assertEqual(command[3:], [
            "--days", "3", "--output-dir", "fit_exports", *argv[1:]
        ])
        self.assertEqual(run.call_args.kwargs, {"check": False})

    def test_all_wrappers_run_help_from_unrelated_working_directory(self):
        scripts = Path(__file__).resolve().parent
        commands = [
            [sys.executable, str(scripts / "run_sync_fit_cron.py"), "--help"],
            [sys.executable, str(scripts / "run_tp_sync_fit_cron.py"), "--help"],
            ["bash", str(scripts / "run_tp_sync_fit.sh"), "--help"],
        ]
        with tempfile.TemporaryDirectory(prefix="tp unrelated cwd ") as directory:
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(
                        command, cwd=directory, capture_output=True, text=True,
                        timeout=15, check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("--output-dir", result.stdout)
                    self.assertIn("sync-fit", result.stdout)


if __name__ == "__main__":
    unittest.main()
