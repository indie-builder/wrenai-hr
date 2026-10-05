"""Snapshot boundary and atomic publication tests use isolated temporary assets."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import duckdb

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/export_dashboard.py"
sys.path.insert(0, str(SCRIPT.parents[2]))
from hr_query.semantic import build_mdl
MODULE_SPEC = importlib.util.spec_from_file_location("dashboard_export_under_test", SCRIPT)
exporter = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(exporter)


class DashboardPublicationTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.app, self.reports = self.root / "apps/hr-overview", self.root / "reports"
        (self.app / "data").mkdir(parents=True)
        self.reports.mkdir()
        for file, text in {"index.html": "old page", "query-spec.json": "old spec", "mdl.json": "old mdl",
                           "snapshot-manifest.json": "old manifest", "data/old.parquet": "old snapshot"}.items():
            (self.app / file).write_text(text)
        self.source, self.database = self.root / "source-mdl.json", self.root / "public.duckdb"
        self.source.write_text("source sentinel")
        self.database.write_bytes(b"database sentinel")
        self.result = self.reports / "result.json"
        self.result.write_bytes(b"old result")
        self.results, self.before = {"q": [{"value": 1}]}, self.app_contents()
        self.stack.enter_context(patch.multiple(exporter, APP=self.app, SOURCE_MDL=self.source,
                                               DATABASE=self.database, SPEC=self.app / "query-spec.json"))
        self.inputs = self.mock(exporter, "read_inputs", return_value=({}, {
            "snapshot_date": "2026-08-31", "tables": {}, "queries": {"q": {}}}))
        self.mock(exporter, "input_hashes", return_value={})
        self.mock(exporter, "prune_mdl", return_value={"models": []})
        self.mock(exporter, "plan_queries", return_value={})
        self.mock(exporter, "manifest_for", return_value={})
        self.check = self.mock(exporter, "check_assets", return_value=self.results)
        self.validate = self.mock(exporter, "validate_results", return_value=self.results)
        self.connect = self.mock(duckdb, "connect", return_value=MagicMock())

    def mock(self, module, name, **options):
        return self.stack.enter_context(patch.object(module, name, **options))

    def app_contents(self):
        return {str(p.relative_to(self.app)): p.read_bytes() for p in self.app.rglob("*") if p.is_file()}

    def run_cli(self, destination=None, check=False):
        args = (["--check"] if check else []) + (["--results-json", str(destination)] if destination else [])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return exporter.main(args)

    def assert_preserved(self):
        self.assertEqual(self.app_contents(), self.before)
        self.assertEqual(self.result.read_bytes(), b"old result")
        self.assertEqual(self.source.read_text(), "source sentinel")
        self.assertEqual(self.database.read_bytes(), b"database sentinel")
        self.assertFalse(list(self.app.parent.glob(".hr-overview-*")))
        self.assertFalse(list(self.reports.glob(".hr-overview-results-*")))

    def assert_rejected(self, targets):
        for target in targets:
            for check in (False, True):
                with self.subTest(target=target, check=check):
                    self.assertEqual(self.run_cli(target, check), 1)
                    self.assert_preserved()
        self.inputs.assert_not_called()
        self.connect.assert_not_called()

    def test_missing_parent_and_directory_destination_preserve_app(self):
        self.assert_rejected([self.root / "missing/result.json", self.reports])

    def test_app_inputs_export_modules_and_lock_paths_are_rejected(self):
        self.assert_rejected([self.app, self.app / "index.html", self.app / "new.json", self.app / "data/old.parquet",
            self.app.parent, self.source, self.database, self.app.parent / ".hr-overview-export.lock", *exporter.EXPORT_SOURCES])

    def test_symlinks_into_app_or_inputs_are_rejected(self):
        alias, source_alias = self.root / "app-alias", self.root / "source-alias.json"
        alias.symlink_to(self.app, target_is_directory=True)
        source_alias.symlink_to(self.source)
        self.assert_rejected([alias / "new.json", alias / "index.html", source_alias])

    def test_unwritable_result_directory_preserves_app(self):
        original = tempfile.TemporaryDirectory
        def create(*args, **kwargs):
            if kwargs.get("dir") == self.reports:
                raise PermissionError("result directory is not writable")
            return original(*args, **kwargs)
        with patch.object(exporter.tempfile, "TemporaryDirectory", side_effect=create):
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

    def test_result_replace_failure_preserves_app_and_result_in_both_modes(self):
        original = Path.replace
        for check in (False, True):
            def replace(path, target):
                if Path(target) == self.result:
                    self.assertEqual(self.app_contents() == self.before, check)
                    raise PermissionError("result replacement denied")
                return original(path, target)
            with self.subTest(check=check), patch.object(Path, "replace", replace):
                self.assertEqual(self.run_cli(self.result, check), 1)
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
        for check, mocked in ((False, self.validate), (True, self.check)):
            with self.subTest(check=check), patch.object(mocked, "side_effect", ValueError("invalid snapshot")):
                self.assertEqual(self.run_cli(self.result, check), 1)
                self.assert_preserved()

    def test_export_input_drift_does_not_publish_result(self):
        with patch.object(exporter, "input_hashes", side_effect=[{}, {"changed": True}]):
            self.assertEqual(self.run_cli(self.result), 1)
        self.assert_preserved()

    def test_success_publishes_app_and_atomically_replaces_result(self):
        original, replacements = Path.replace, []
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
        self.connect.assert_called_once_with(str(self.database), read_only=True)
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
        self.connect.assert_called_once_with(str(self.database), read_only=True)


class DashboardBoundaryTests(unittest.TestCase):
    def test_pruning_excludes_private_fields_and_only_removes_descriptions(self):
        spec = json.loads(exporter.SPEC.read_text())
        source = build_mdl(exporter.SOURCE_MDL.parent.parent)
        department = next(m for m in source["models"] if m["name"] == "departments")
        department["properties"]["execution_flag"] = "keep"
        before = copy.deepcopy(source)
        mdl = exporter.prune_mdl(source, spec)
        self.assertEqual(source, before)
        self.assertEqual((len(mdl["models"]), sum(map(len, exporter.physical_columns(mdl).values()))), (12, 53))
        names = {c["name"] for m in mdl["models"] for c in m["columns"]}
        self.assertFalse(names & {"emp_name", "birth_date", "email", "phone", "cand_name"})
        self.assertEqual(mdl["models"][0]["properties"], {"execution_flag": "keep"})
        self.assertNotIn('"description"', json.dumps(mdl))

    def test_exporter_fingerprint_covers_all_modules(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = [Path(temporary) / name for name in ("source", "spec", "cli", "semantics", "snapshot", "compare")]
            for path in paths:
                path.write_text("initial")
            with patch.object(exporter, "EXPORT_SOURCES", tuple(paths[2:])), patch.object(exporter, "ROOT", Path(temporary)):
                initial = exporter.input_hashes(*paths[:2])
                for path in paths[2:]:
                    path.write_text("updated")
                    self.assertNotEqual(initial["exporter_sha256"], exporter.input_hashes(*paths[:2])["exporter_sha256"])
                    path.write_text("initial")

    def test_result_contract_rejects_empty_order_field_and_nonfinite_differences(self):
        exporter.assert_rows([{"v": 1.011}], [{"v": 1}], "boundary")
        for actual, expected in [([], []), ([{"v": 1.01101}], [{"v": 1}]),
            ([{"v": float("nan")}], [{"v": float("nan")}]), ([{"v": None}], [{"v": 0}]),
            ([{"v": 1}], [{"other": 1}]), ([{"v": 1}, {"v": 2}], [{"v": 2}, {"v": 1}])]:
            with self.subTest(actual=actual), self.assertRaises(ValueError):
                exporter.assert_rows(actual, expected, "invalid")


if __name__ == "__main__":
    unittest.main()
