#!/usr/bin/env python3
"""Check locked, non-editable installs outside the checkout, including isolated workers."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(argv, **kwargs):
    subprocess.run(list(map(str, argv)), check=True, **kwargs)


def main():
    with tempfile.TemporaryDirectory(prefix="hr-install-") as folder:
        temporary = Path(folder)
        for profile in ("service", "analysis"):
            environment = temporary / profile
            settings = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(environment)}
            options = ["--no-dev"] if profile == "service" else ["--no-default-groups", "--group", "analysis"]
            run(["uv", "sync", "--project", ROOT, "--locked", "--no-editable", *options], env=settings)
            python = environment / "bin/python"
            code = '''import importlib.util, pathlib, sys
import hr_contracts, hr_query, hr_analytics, hr_mcp
for package in (hr_contracts, hr_query, hr_analytics, hr_mcp):
    assert pathlib.Path(package.__file__).is_relative_to(sys.prefix), package.__file__
'''
            if profile == "service":
                code += '''assert importlib.util.find_spec('mcp') and importlib.util.find_spec('fastapi')
assert importlib.util.find_spec('wren') is None and importlib.util.find_spec('pyarrow') is None
import sysconfig
size = sum(p.stat().st_size for root in (pathlib.Path(sysconfig.get_path('purelib')), pathlib.Path(sys.argv[1])) for p in root.rglob('*') if p.is_file())
assert size < 450_000_000, size
print(f'Service dependencies and private bundle: {size} bytes')
from hr_mcp.engine import AnalyticsEngine
from hr_mcp.contracts import MCPQueryError
engine = AnalyticsEngine(pathlib.Path(sys.argv[1]))
assert engine.query_sql("SELECT count(*) FROM employees WHERE status = '在职'")['rows'] == [['528']]
assert engine.plan_sql('SELECT count(*) FROM employees')['executed'] is False
assert engine.query_cube('workforce', ['headcount'], [])['rows'] == [['528']]
try:
    engine.query_sql('DELETE FROM employees')
except MCPQueryError as error:
    assert error.code == 'SQL_REJECTED'
else:
    raise AssertionError('write accepted')
'''
            else:
                code += '''assert importlib.util.find_spec('wren') and importlib.util.find_spec('pyarrow')
assert all(importlib.util.find_spec(name) is None for name in ('mcp', 'fastapi', 'uvicorn'))
from hr_analytics.execution import run_gt
result = run_gt('SELECT count(*) FROM employees', db_file=sys.argv[1])
from hr_contracts.tables import parse_csv
assert result.ok and parse_csv(result.stdout) == (['count_star()'], [['786']]), result
'''
            (temporary / "hr_query.py").write_text("raise RuntimeError('cwd poisoning')")
            run([python, "-I", "-c", code, ROOT / "hr_mcp/data/public.duckdb" if profile == "analysis" else ROOT / "hr_mcp/data"],
                cwd=temporary, env={**os.environ, "PYTHONPATH": str(temporary)})
            if profile == "analysis":
                run([python, ROOT / "hr-demo/scripts/export_dashboard.py", "--check",
                     "--database", ROOT / "hr_mcp/data/public.duckdb"], cwd=temporary)
            print(f"PASS {profile} non-editable installed artifact", flush=True)


if __name__ == "__main__":
    main()
