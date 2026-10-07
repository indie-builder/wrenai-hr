"""Architecture checks detect forbidden imports and runner exports survive relocation."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile
import unittest

from _support import load_module

ROOT = Path(__file__).resolve().parents[2]
architecture = load_module(ROOT / "scripts/check_architecture.py")
exporter = load_module(ROOT / "scripts/export_runner.py")


class ArchitectureTests(unittest.TestCase):
    def test_question_archive_replaces_stale_answers(self):
        verify = load_module(ROOT / "scripts/verify.py")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / "fresh"
            package.mkdir()
            (package / "questions.json").write_text("[]")
            destination = root / "nl-package.zip"
            with zipfile.ZipFile(destination, "w") as archive:
                archive.writestr("answers.csv", "private answer")
            verify.publish_question_package(package, destination)
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(archive.namelist(), ["questions.json"])
                self.assertEqual(archive.read("questions.json"), b"[]")

    def test_contract_cannot_import_execution_or_change_import_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / "src/hr_contracts"
            package.mkdir(parents=True)
            (package / "bad.py").write_text("import hr_analytics.execution\nimport sys\nsys.path.insert(0, 'validation')\n")
            errors = architecture.violations(root)
            self.assertTrue(any("cannot import hr_analytics.execution" in error for error in errors))
            self.assertTrue(any("instead of changing sys.path" in error for error in errors))

    @unittest.skipUnless(importlib.util.find_spec("duckdb"), "requires analysis environment")
    def test_exported_runner_executes_outside_checkout(self):
        import duckdb
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runner = exporter.export_runner(root / "portable")
            self.assertFalse(any(path.is_symlink() for path in runner.rglob("*")))
            database = root / "public.duckdb"
            with duckdb.connect(str(database)) as connection:
                connection.execute("CREATE TABLE sample AS SELECT 7 AS n")
            questions = root / "questions.py"
            questions.write_text("QUESTIONS = [{'id':'q1','gt':'SELECT n FROM sample','wren':'SELECT n FROM sample'}]\n")
            wren = root / "wren"
            wren.write_text("#!/bin/sh\nprintf 'n\\n7\\n'\n")
            wren.chmod(0o755)
            driver = root / "drive.py"
            driver.write_text(
                "import sys, pathlib, runpy\n"
                f"sys.path.insert(0, {str(runner)!r})\n"
                "import hr_contracts, hr_analytics, hr_query\n"
                f"assert all(pathlib.Path(m.__file__).is_relative_to({str(runner)!r}) for m in (hr_contracts, hr_analytics, hr_query))\n"
                f"runpy.run_path({str(runner / 'run_all.py')!r}, run_name='__main__')\n")
            result = subprocess.run([sys.executable, str(driver), "--questions", str(questions),
                                     "--project", str(root), "--db", str(database)], cwd=root,
                                    env={"PATH": str(root), "WREN_BIN": str(wren)},
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("1/1 PASS", result.stdout)
            self.assertEqual((root / "results/q1.gt.csv").read_text(), "n\n7\n")
