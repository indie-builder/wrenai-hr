"""Bundle replacement, complete fixed-SQL replay and standalone deployment."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from unittest import mock

from fixtures import ROOT, copy_deployment, fixture, load_module
from hr_mcp.contracts import BUNDLE_FILES, BUNDLE_FORMAT_VERSION, MCPQueryError
from hr_mcp.engine import AnalyticsEngine, file_digest
from scripts.prepare_mcp import build_bundle, write_json


class BundleTests(unittest.TestCase):
    def test_missing_and_corrupted_metadata_are_safe_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "bundle"
            with self.assertRaises(MCPQueryError) as error:
                AnalyticsEngine(data)
            self.assertEqual(error.exception.code, "BUNDLE_UNAVAILABLE")
            fixture(data)
            (data / "context.json").write_text('{"private":"must not leak"}')
            with self.assertRaises(MCPQueryError) as error:
                AnalyticsEngine(data)
            self.assertEqual(error.exception.code, "BUNDLE_UNAVAILABLE")

    def test_failed_build_preserves_previous_bundle_and_local_db(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "bundle"
            fixture(data)
            before = file_digest(data / "public.duckdb")
            original = ROOT / "hr-demo/db/duckdb/public.duckdb"
            original_hash = file_digest(original) if original.exists() else None
            with mock.patch("scripts.prepare_mcp.build_database", side_effect=ValueError("invalid seed")):
                with self.assertRaises(ValueError):
                    build_bundle(data)
            self.assertEqual(file_digest(data / "public.duckdb"), before)
            self.assertEqual(file_digest(original) if original.exists() else None, original_hash)

    def test_unrelated_extra_and_corrupt_outputs_are_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "unrelated"
            data.mkdir()
            (data / "keep.txt").write_text("keep")
            with self.assertRaises(ValueError):
                build_bundle(data)
            self.assertEqual((data / "keep.txt").read_text(), "keep")
            data = Path(temporary) / "bundle"
            fixture(data)
            (data / "keep.txt").write_text("keep")
            with self.assertRaises(ValueError):
                build_bundle(data)
            self.assertEqual((data / "keep.txt").read_text(), "keep")
            (data / "keep.txt").unlink()
            (data / "context.json").write_text("{}")
            with self.assertRaises(ValueError):
                build_bundle(data)
            self.assertEqual((data / "context.json").read_text(), "{}")

    def test_real_bundle_upgrades_v1_and_replays_all_fixed_queries(self):
        from hr_query.duckdb_worker import query

        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "bundle"
            original = ROOT / "hr-demo/db/duckdb/public.duckdb"
            original_hash = file_digest(original) if original.exists() else None
            fixture(data)
            # An intact previous bundle can be upgraded without retaining code
            # in the data directory. New bundles are data-only format 2.
            shutil.copyfile(ROOT / "hr_query/sql_policy.py", data / "sql_policy.py")
            shutil.copyfile(ROOT / "hr_query/duckdb_worker.py", data / "sql_worker.py")
            previous = json.loads((data / "manifest.json").read_text())
            previous["format_version"] = 1
            previous["files"].update({name: file_digest(data / name)
                                      for name in ("sql_policy.py", "sql_worker.py")})
            write_json(data / "manifest.json", previous)
            manifest = build_bundle(data)
            self.assertEqual(manifest["format_version"], BUNDLE_FORMAT_VERSION)
            self.assertEqual(set(manifest["files"]), set(BUNDLE_FILES))
            self.assertEqual({path.name for path in data.iterdir()}, {*BUNDLE_FILES, "manifest.json"})
            self.assertEqual(len(manifest["table_rows"]), 25)
            self.assertEqual(sum(manifest["table_rows"].values()), 273515)
            self.assertEqual(manifest["files"]["public.duckdb"], file_digest(data / "public.duckdb"))
            self.assertFalse(any("knowledge/sql" in path for path in manifest["sources"]))
            for source in ("hr_query/sql_policy.py", "hr_query/duckdb_worker.py", "hr_mcp/contracts.py",
                           "hr_mcp/server.py", "pyproject.toml", "uv.lock", "vercel.json"):
                self.assertIn(source, manifest["sources"])
            self.assertEqual((data / "public.duckdb").stat().st_mode & 0o222, 0)
            self.assertEqual(file_digest(original) if original.exists() else None, original_hash)
            analytics = AnalyticsEngine(data)
            self.assertEqual(analytics.query_sql("SELECT COUNT(*) AS n FROM employees")["rows"], [["786"]])
            sys.path.insert(0, str(ROOT / "hr-demo/validation/v2"))
            try:
                regression = load_module(ROOT / "hr-demo/validation/v2/run_all.py", "_mcp_regression")
            finally:
                sys.path.pop(0)
            self.assertEqual(len(regression.QUESTIONS), 41)
            for question in regression.QUESTIONS:
                with self.subTest(question=question["id"]):
                    actual = analytics.query_sql(question["wren"])
                    expected = query(str(data / "public.duckdb"), question["gt"])
                    ok, message, _ = regression.compare(
                        regression.table_csv(expected["columns"], expected["rows"]),
                        regression.table_csv(actual["columns"], actual["rows"]),
                        **regression.comparison_options(question),
                    )
                    self.assertTrue(ok, f"{question['id']}: {message}")
            deployed = Path(temporary) / "isolated"
            copy_deployment(deployed)
            data.rename(deployed / "hr_mcp/data")
            probe = subprocess.run(
                [sys.executable, "-I", "-B", str(deployed / "hr_mcp/worker.py"), str(deployed / "hr_mcp/data")],
                input=json.dumps({"operation": "query", "sql": "SELECT COUNT(*) AS n FROM employees"}),
                text=True, capture_output=True, timeout=20, cwd=temporary,
            )
            self.assertEqual(probe.returncode, 0)
            self.assertEqual(json.loads(probe.stdout)["result"]["rows"], [["786"]])

    def test_vercel_vendor_dependencies_in_isolated_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary) / "bundle"
            fixture(data)
            deployed = Path(temporary) / "deployment"
            copy_deployment(deployed)
            environment = Path(temporary) / "clean-python"
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)],
                           check=True, capture_output=True, timeout=20)
            command = [str(environment / "bin/python"), "-I", "-B", str(deployed / "hr_mcp/worker.py"), str(data)]

            def call(sql):
                result = subprocess.run(command, input=json.dumps({"operation": "query", "sql": sql}),
                                        text=True, capture_output=True, timeout=20, cwd=temporary,
                                        env={"MCP_AUTH_TOKEN": "must-not-be-needed", "PYTHONPATH": "/invalid"})
                self.assertEqual(result.returncode, 0)
                return json.loads(result.stdout)

            self.assertEqual(call("SELECT COUNT(*) AS n FROM employees")["error"]["code"], "QUERY_FAILED")
            (deployed / "_vendor").symlink_to(sysconfig.get_path("purelib"), target_is_directory=True)
            self.assertEqual(call("SELECT COUNT(*) AS n FROM employees")["result"]["rows"], [["3"]])
            self.assertEqual(call("DELETE FROM employees")["error"]["code"], "SQL_REJECTED")


if __name__ == "__main__":
    unittest.main()
