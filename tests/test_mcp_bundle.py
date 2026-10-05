"""Bundle replacement, fixed-SQL replay and isolated deployment adapters."""
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from unittest import mock

from fixtures import ErrorAssertions, ROOT, copy_deployment, fixture, load_module, worker_call
from hr_mcp.contracts import BUNDLE_FILES, BUNDLE_FORMAT_VERSION
from hr_mcp.engine import AnalyticsEngine, file_digest
from scripts.prepare_mcp import build_bundle


class BundleTests(ErrorAssertions, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.data = self.directory / "bundle"
        self.original = ROOT / "hr-demo/db/duckdb/public.duckdb"
        self.original_hash = self.local_hash()
        self.addCleanup(lambda: self.assertEqual(self.local_hash(), self.original_hash))

    def local_hash(self):
        return file_digest(self.original) if self.original.exists() else None

    def test_missing_and_corrupted_metadata_are_safe_errors(self):
        self.assert_code("BUNDLE_UNAVAILABLE", AnalyticsEngine, self.data)
        fixture(self.data)
        (self.data / "context.json").write_text('{"private":"must not leak"}')
        self.assert_code("BUNDLE_UNAVAILABLE", AnalyticsEngine, self.data)

    def test_invalid_output_and_failed_build_preserve_previous_files(self):
        self.data.mkdir()
        kept = self.data / "keep.txt"
        kept.write_text("keep")
        with self.assertRaises(ValueError):
            build_bundle(self.data)
        self.assertEqual(kept.read_text(), "keep")
        shutil.rmtree(self.data)
        fixture(self.data)
        before = file_digest(self.data / "public.duckdb")
        with mock.patch("scripts.prepare_mcp.build_database", side_effect=ValueError("invalid seed")):
            with self.assertRaises(ValueError):
                build_bundle(self.data)
        self.assertEqual(file_digest(self.data / "public.duckdb"), before)
        for path, content in ((kept, "keep"), (self.data / "context.json", "{}")):
            path.write_text(content)
            with self.assertRaises(ValueError):
                build_bundle(self.data)
            self.assertEqual(path.read_text(), content)
            if path == kept:
                path.unlink()

    def test_real_build_skips_target_and_replays_all_fixed_queries(self):
        from hr_query.duckdb_worker import query

        original_read = Path.read_text
        def without_target(path, *args, **kwargs):
            self.assertFalse("target" in path.parts, "bundle build read a generated cache")
            return original_read(path, *args, **kwargs)
        with mock.patch.object(Path, "read_text", without_target):
            manifest = build_bundle(self.data)
        self.assertEqual(manifest["format_version"], BUNDLE_FORMAT_VERSION)
        self.assertEqual(set(manifest["files"]), set(BUNDLE_FILES))
        self.assertEqual({path.name for path in self.data.iterdir()}, {*BUNDLE_FILES, "manifest.json"})
        self.assertEqual((len(manifest["table_rows"]), sum(manifest["table_rows"].values())), (25, 273515))
        self.assertEqual(manifest["files"]["public.duckdb"], file_digest(self.data / "public.duckdb"))
        self.assertFalse(any("knowledge/sql" in path or "/target/" in path for path in manifest["sources"]))
        for source in ("hr_query/sql_policy.py", "hr_query/duckdb_worker.py", "hr_query/semantic.py",
                       "hr_mcp/contracts.py", "hr_mcp/server.py", "scripts/mcp_context.py",
                       "hr-demo/wren-project/wren_project.yml", "pyproject.toml", "uv.lock", "vercel.json"):
            self.assertIn(source, manifest["sources"])
        seeds = {path for path in manifest["sources"] if path.startswith("hr-demo/db/seed/")}
        self.assertEqual(seeds, {"hr-demo/db/seed/manifest.json", *(
            f"hr-demo/db/seed/{table}.parquet" for table in manifest["table_rows"])})
        self.assertEqual((self.data / "public.duckdb").stat().st_mode & 0o222, 0)
        analytics = AnalyticsEngine(self.data)
        self.assertEqual(analytics.query_sql("SELECT COUNT(*) AS n FROM employees")["rows"], [["786"]])
        sys.path.insert(0, str(ROOT / "hr-demo/validation/v2"))
        try:
            from result_contract import compare, comparison_options, table_csv

            regression = load_module(ROOT / "hr-demo/validation/v2/run_all.py", "_mcp_regression")
        finally:
            sys.path.pop(0)
        self.assertEqual(len(regression.QUESTIONS), 41)
        for question in regression.QUESTIONS:
            with self.subTest(question=question["id"]):
                actual = analytics.query_sql(question["wren"])
                expected = query(str(self.data / "public.duckdb"), question["gt"])
                ok, message, _ = compare(
                    table_csv(expected["columns"], expected["rows"]),
                    table_csv(actual["columns"], actual["rows"]), **comparison_options(question))
                self.assertTrue(ok, f"{question['id']}: {message}")
        deployed = self.directory / "isolated"
        copy_deployment(deployed)
        self.data.rename(deployed / "hr_mcp/data")
        result = worker_call(deployed / "hr_mcp/data", {"operation": "query", "sql": "SELECT COUNT(*) FROM employees"},
                             root=deployed, cwd=self.directory)
        self.assertEqual(result["result"]["rows"], [["786"]])

    def test_vercel_vendor_dependencies_in_isolated_worker(self):
        fixture(self.data)
        deployed, environment = self.directory / "deployment", self.directory / "clean-python"
        copy_deployment(deployed)
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)],
                       check=True, capture_output=True, timeout=20)
        def call(sql):
            return worker_call(self.data, {"operation": "query", "sql": sql}, root=deployed,
                               python=environment / "bin/python", cwd=self.directory,
                               env={"MCP_AUTH_TOKEN": "must-not-be-needed", "PYTHONPATH": "/invalid"})
        self.assertEqual(call("SELECT COUNT(*) FROM employees")["error"]["code"], "QUERY_FAILED")
        (deployed / "_vendor").symlink_to(sysconfig.get_path("purelib"), target_is_directory=True)
        self.assertEqual(call("SELECT COUNT(*) FROM employees")["result"]["rows"], [["3"]])
        self.assertEqual(call("DELETE FROM employees")["error"]["code"], "SQL_REJECTED")
