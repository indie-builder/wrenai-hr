"""Export publication tests using only temporary files and mocked query results."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import duckdb

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/export_dashboard.py"
MODULE_SPEC = importlib.util.spec_from_file_location("dashboard_export_under_test", SCRIPT)
exporter = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(exporter)


class DashboardPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.app = self.root / "apps/hr-overview"
        (self.app / "data").mkdir(parents=True)
        (self.app / "index.html").write_text("old page")
        (self.app / "query-spec.json").write_text("old spec")
        (self.app / "mdl.json").write_text("old mdl")
        (self.app / "snapshot-manifest.json").write_text("old manifest")
        (self.app / "data/old.parquet").write_bytes(b"old snapshot")
        self.source = self.root / "source-mdl.json"
        self.database = self.root / "public.duckdb"
        self.source.write_text("source sentinel")
        self.database.write_bytes(b"database sentinel")
        self.reports = self.root / "reports"
        self.reports.mkdir()
        self.result = self.reports / "result.json"
        self.result.write_bytes(b"old result")
        self.results = {"q": [{"value": 1}]}
        self.before = self.app_contents()
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in {
            "APP": self.app, "SOURCE_MDL": self.source,
            "DATABASE": self.database, "SPEC": self.app / "query-spec.json",
        }.items():
            self.stack.enter_context(patch.object(exporter, name, value))
        self.inputs = self.stack.enter_context(patch.object(
            exporter, "read_inputs", return_value=({}, {
                "snapshot_date": "2026-08-31", "tables": {}, "queries": {"q": {}}
            })))
        self.stack.enter_context(patch.object(exporter, "input_hashes", return_value={}))
        self.stack.enter_context(patch.object(exporter, "prune_mdl", return_value={"models": []}))
        self.stack.enter_context(patch.object(exporter, "plan_queries", return_value={}))
        self.stack.enter_context(patch.object(exporter, "validate_empty_schema"))
        self.stack.enter_context(patch.object(exporter, "physical_columns", return_value={}))
        self.check = self.stack.enter_context(patch.object(exporter, "check_assets", return_value=self.results))
        self.stack.enter_context(patch.object(exporter.importlib.metadata, "version", return_value="test"))
        self.connection = MagicMock()
        self.connect = self.stack.enter_context(patch.object(duckdb, "connect", return_value=self.connection))

    def app_contents(self):
        return {str(path.relative_to(self.app)): path.read_bytes()
                for path in self.app.rglob("*") if path.is_file()}

    def run_cli(self, destination=None, check=False):
        args = ["--check"] if check else []
        if destination is not None:
            args += ["--results-json", str(destination)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return exporter.main(args)

    def assert_preserved(self):
        self.assertEqual(self.app_contents(), self.before)
        self.assertEqual(self.result.read_bytes(), b"old result")
        self.assertEqual(self.source.read_text(), "source sentinel")
        self.assertEqual(self.database.read_bytes(), b"database sentinel")
        self.assertFalse(list(self.app.parent.glob(".hr-overview-*")))
        self.assertFalse(list(self.reports.glob(".hr-overview-results-*")))

    def test_missing_parent_and_directory_destination_preserve_app(self):
        for target in (self.root / "missing/result.json", self.reports):
            for check in (False, True):
                with self.subTest(target=target, check=check):
                    self.assertEqual(self.run_cli(target, check), 1)
                    self.assert_preserved()
        self.inputs.assert_not_called()
        self.connect.assert_not_called()

    def test_unwritable_result_directory_preserves_app(self):
        original = tempfile.TemporaryDirectory

        def create_directory(*args, **kwargs):
            if kwargs.get("dir") == self.reports:
                raise PermissionError("result directory is not writable")
            return original(*args, **kwargs)

        with patch.object(exporter.tempfile, "TemporaryDirectory", side_effect=create_directory):
            self.assertEqual(self.run_cli(self.result), 1)
        self.assert_preserved()

    def test_partial_result_write_preserves_old_app_and_result(self):
        original = Path.write_bytes

        def write(path, data):
            if path.parent.name.startswith(".hr-overview-results-"):
                original(path, b"partial")
                raise OSError("result write failed")
            return original(path, data)

        with patch.object(Path, "write_bytes", write):
            self.assertEqual(self.run_cli(self.result), 1)
        self.assert_preserved()

    def test_result_replace_failure_restores_old_app_and_result(self):
        original = Path.replace

        def replace(path, target):
            if Path(target) == self.result:
                # Exercise rollback after the new app has actually been installed.
                self.assertNotEqual(self.app_contents(), self.before)
                raise PermissionError("result replacement denied")
            return original(path, target)

        with patch.object(Path, "replace", replace):
            self.assertEqual(self.run_cli(self.result), 1)
        self.assert_preserved()

    def test_app_install_failure_preserves_old_app_and_result(self):
        original = Path.rename

        def rename(path, target):
            if path.name == "app" and Path(target) == self.app:
                raise OSError("app installation failed")
            return original(path, target)

        with patch.object(Path, "rename", rename):
            self.assertEqual(self.run_cli(self.result), 1)
        self.assert_preserved()

    def test_validation_failure_does_not_publish_result(self):
        self.check.side_effect = ValueError("invalid snapshot")
        self.assertEqual(self.run_cli(self.result), 1)
        self.assert_preserved()

    def test_app_input_and_lock_paths_are_rejected_before_reading_inputs(self):
        targets = [self.app, self.app / "index.html", self.app / "new.json",
                   self.app / "data/old.parquet", self.app.parent, self.source,
                   self.database, self.app / "query-spec.json", SCRIPT,
                   self.app.parent / ".hr-overview-export.lock"]
        for target in targets:
            for check in (False, True):
                with self.subTest(target=target, check=check):
                    self.assertEqual(self.run_cli(target, check), 1)
                    self.assert_preserved()
        self.inputs.assert_not_called()
        self.connect.assert_not_called()

    def test_symlinks_into_app_or_inputs_are_rejected(self):
        alias = self.root / "app-alias"
        alias.symlink_to(self.app, target_is_directory=True)
        source_alias = self.root / "source-alias.json"
        source_alias.symlink_to(self.source)
        for target in (alias / "new.json", alias / "index.html", source_alias):
            with self.subTest(target=target):
                self.assertEqual(self.run_cli(target), 1)
                self.assert_preserved()
        self.inputs.assert_not_called()
        self.connect.assert_not_called()

    def test_success_publishes_app_and_atomically_replaces_result(self):
        original = Path.replace
        replacements = []

        def replace(path, target):
            if Path(target) == self.result:
                self.assertEqual(self.result.read_bytes(), b"old result")
                self.assertEqual(path.read_bytes(), exporter.canonical(self.results))
                replacements.append(target)
            return original(path, target)

        with patch.object(Path, "replace", replace):
            self.assertEqual(self.run_cli(self.result), 0)
        self.assertEqual(replacements, [self.result])
        self.assertEqual(self.result.read_bytes(), exporter.canonical(self.results))
        self.assertEqual((self.app / "index.html").read_text(), "old page")
        self.assertNotEqual(self.app_contents(), self.before)
        self.assertFalse(list(self.app.parent.glob(".hr-overview-*")))
        self.assertFalse(list(self.reports.glob(".hr-overview-results-*")))

    def test_export_without_results_still_publishes_app(self):
        self.assertEqual(self.run_cli(), 0)
        self.assertNotEqual(self.app_contents(), self.before)
        self.assertEqual(self.result.read_bytes(), b"old result")

    def test_check_writes_results_atomically_without_changing_app(self):
        self.assertEqual(self.run_cli(self.result, check=True), 0)
        self.assertEqual(self.result.read_bytes(), exporter.canonical(self.results))
        self.assertEqual(self.app_contents(), self.before)

    def test_check_result_replace_failure_preserves_old_result(self):
        with patch.object(Path, "replace", side_effect=PermissionError("result replacement denied")):
            self.assertEqual(self.run_cli(self.result, check=True), 1)
        self.assert_preserved()


if __name__ == "__main__":
    unittest.main()
