"""Exercise the actual isolated stdin/stdout seam and its response decoder."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock

from fixtures import BundleCase, worker_call
from hr_mcp.contracts import MAX_INPUT_BYTES, SNAPSHOT_DATE, decode_worker_response


class WorkerTests(BundleCase):
    def call(self, request):
        return worker_call(self.data, request)
    def test_plan_query_and_cube_return_complete_results(self):
        for request, rows in (
            ({"operation": "plan", "sql": "SELECT COUNT(*) FROM employees"}, []),
            ({"operation": "query", "sql": "SELECT COUNT(*) FROM employees"}, [["3"]]),
            ({"operation": "cube", "cube_query": {"cube": "workforce", "measures": ["headcount"], "dimensions": []}}, [["2"]]),
        ):
            with self.subTest(operation=request["operation"]):
                result = decode_worker_response(json.dumps(self.call(request)).encode(), request["operation"])
                self.assertEqual(result["rows"], rows)
                self.assertTrue(result["complete"])
                self.assertEqual(result["snapshot_date"], SNAPSHOT_DATE)
                if request["operation"] == "plan":
                    self.assertFalse(result["executed"])
                    self.assertIn("employees", result["planned_sql"])

    def test_invalid_inputs_have_safe_error_envelopes(self):
        for request in (b"{", b"[]", b"x" * (MAX_INPUT_BYTES + 1), {},
                        {"operation": "delete"}, {"operation": "cube", "cube_query": []}):
            with self.subTest(kind=type(request).__name__):
                self.assertEqual(self.call(request), {"error": {"code": "INVALID_ARGUMENT"}})
        self.assertEqual(self.call({"operation": "query", "sql": "DELETE FROM employees"}),
                         {"error": {"code": "SQL_REJECTED"}})

    def test_decoder_rejects_truncated_incomplete_and_mismatched_results(self):
        result = {"snapshot_date": SNAPSHOT_DATE, "columns": ["n"], "rows": [["1"]],
                  "row_count": 1, "complete": True}
        payloads = [b"{", b"[]", b'{"result":null}', b'{"error":[],"result":{}}']
        for change in ({"complete": False}, {"snapshot_date": "2026-09-01"},
                       {"rows": [[1]]}, {"rows": [[]]}, {"row_count": True}, {"row_count": 2}):
            payloads.append(json.dumps({"result": {**result, **change}}).encode())
        for raw in payloads:
            with self.subTest(raw=raw):
                self.assert_code("INVALID_RESULT", decode_worker_response, raw, "query")
        self.assert_code("QUERY_FAILED", decode_worker_response, b'{"error":{"code":"private-driver-error"}}', "query")
        self.assert_code("INVALID_RESULT", decode_worker_response, json.dumps({"result": result}).encode(), "plan")

    def test_running_process_timeout_reaps_child_and_allows_next_query(self):
        real_run = subprocess.run
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "busy.py"
            pid_file = Path(temporary) / "pid"
            script.write_text("import os, sys\nfrom pathlib import Path\n"
                              "Path(sys.argv[1]).write_text(str(os.getpid()))\n"
                              "while True:\n    pass\n")

            def run_busy(command, **kwargs):
                return real_run([sys.executable, "-I", "-B", str(script), str(pid_file)],
                                **{**kwargs, "timeout": 0.5})

            with mock.patch("hr_mcp.engine.subprocess.run", side_effect=run_busy):
                self.assert_code("QUERY_TIMEOUT", self.engine.query_sql, "SELECT 1")
            self.assertTrue(pid_file.is_file(), "The child must start executing before timeout")
            with self.assertRaises(ProcessLookupError):
                os.kill(int(pid_file.read_text()), 0)
        self.assertEqual(self.engine.query_sql("SELECT 1")["rows"], [["1"]])
