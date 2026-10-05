"""Semantic source validation and isolated compiler checks."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_semantics as semantics
import query_execution as execution


class SemanticCheckTests(unittest.TestCase):
    def test_duplicate_nested_key_fails_while_valid_yaml_still_loads(self):
        import yaml
        with self.assertRaises(ValueError):
            yaml.load("properties:\n  description: one\n  description: two\n", Loader=semantics.UniqueKeyLoader)
        self.assertEqual(yaml.load("value: 1\n", Loader=semantics.UniqueKeyLoader), {"value": 1})

    def test_known_snapshot_columns_and_clock_use_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "models/employees").mkdir(parents=True)
            (project / "models/employees/metadata.yml").write_text(
                "columns:\n- name: age\n  expression: date_part('year', current_date)\n")
            errors = semantics.check_project(project)
            self.assertTrue(any("运行时日期" in error for error in errors))
            self.assertTrue(any("tenure_years" in error for error in errors))
            (project / "knowledge/sql").mkdir(parents=True)
            (project / "knowledge/sql/fixture.md").write_text("---\nnl: test\nsql: SELECT current_date\n---\n")
            self.assertTrue(any("知识示例SQL使用运行时日期" in error for error in semantics.check_project(project)))

    def test_isolated_build_detects_stale_target_without_changing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "target").mkdir()
            (project / "wren_project.yml").write_text("name: fixture\n")
            original = '{"models": []}'
            (project / "target/mdl.json").write_text(original)
            expected = {"models": [{"name": "changed"}]}

            def build(argv, *, cwd, env):
                self.assertNotEqual(cwd, project)
                self.assertNotEqual(env["WREN_HOME"], str(Path.home() / ".wren"))
                (cwd / "target").mkdir()
                (cwd / "target/mdl.json").write_text(json.dumps(expected))
                return execution.Execution("Built\n", 0)

            with patch.object(semantics, "run_process", side_effect=build), \
                    patch.object(semantics, "build_mdl", return_value=expected):
                self.assertTrue(any("不一致" in error for error in semantics.check_build(project)))
                self.assertEqual((project / "target/mdl.json").read_text(), original)
                (project / "target/mdl.json").unlink()
                self.assertEqual(semantics.check_build(project), [])
                self.assertFalse((project / "target/mdl.json").exists())


if __name__ == "__main__":
    unittest.main()
