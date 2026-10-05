"""Supported YAML edge cases agree with the installed official Wren compiler."""
import _support
from pathlib import Path
import tempfile
import unittest

from hr_query.semantic import build_mdl
from scripts.mcp_context import public_context
from wren.context import build_json


class SemanticCompilerTests(unittest.TestCase):
    def test_optional_empty_files_and_member_lists_preserve_official_semantics(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            files = {
                "wren_project.yml": "name: fixture\nschema_version: '5'\ndata_source: duckdb\n",
                "models/employees/metadata.yml": "name: employees\nref_sql: SELECT 1 AS id\ncolumns: [{name: id, type: INTEGER}]\n",
                "models/employees/ref_sql.sql": "   \n",
                "views/active/metadata.yml": "name: active\nstatement: SELECT * FROM employees\n",
                "cubes/counts/metadata.yml": "name: counts\nbase_object: employees\nmeasures: [{name: count, expression: 'COUNT(*)', type: BIGINT}]\ndimensions:\ntime_dimensions:\n",
            }
            for name, text in files.items():
                path = project / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            for sql in ("{}\n", "", "statement:\n", "statement: ''\n"):
                for relationships in ("relationships:\n", "{}\n", None):
                    with self.subTest(sql=sql, relationships=relationships):
                        (project / "views/active/sql.yml").write_text(sql)
                        path = project / "relationships.yml"
                        if relationships is None:
                            path.unlink(missing_ok=True)
                        else:
                            path.write_text(relationships)
                        actual = build_mdl(project)
                        self.assertEqual(actual, build_json(project))
                        context = public_context(actual, project)
                        self.assertEqual(context["relationships"], [])
                        self.assertEqual(context["cubes"][0]["dimensions"], [])
                        self.assertEqual(context["cubes"][0]["time_dimensions"], [])
                        self.assertEqual(context["views"][0]["columns"], [{"name": "*"}])


if __name__ == "__main__":
    unittest.main()
