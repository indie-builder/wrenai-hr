"""Shared bootstrap for the v2 suite: import roots, module loading, guard fixtures.

`unittest discover -s` puts this tests directory first on sys.path, so every
test module can simply `import _support` (same mechanism as
scripts/tests/_support.py). Deliberately no __init__.py: the suite relies on
flat-module discovery identity.
"""
import importlib.util
from pathlib import Path
import sys

TESTS = Path(__file__).resolve().parent
V2 = TESTS.parent                # check_semantics / query_execution / questions / run_all / result_contract
HR_DEMO = TESTS.parents[2]       # hr-demo root: db/build_duckdb.py, scripts/export_dashboard.py
REPO_ROOT = TESTS.parents[3]     # repo root: hr_query, scripts, wren (test_semantic_compiler)
for _root in (REPO_ROOT, V2 / "eval", V2):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))


def load_module(path, name=None):
    """Load a script by file path; it is not importable as a package module."""
    spec = importlib.util.spec_from_file_location(name or Path(path).stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Worker 攻击向量：run_gt 子进程（test_validation.WorkerTests）与真实规划集成
# （test_nl_eval.WrenIntegrationTests）共用；根目录套件的等价常量在 tests/fixtures.py
# （跨套件不共享，venv 不同）。各自的引号临时目录前缀保留——分别覆盖不同执行路径。
WORKER_ATTACK_SQL = (
    "DELETE FROM employees",
    "SELECT * FROM read_csv_auto('/etc/passwd')",
    "SELECT 1; SELECT 2",
)


def create_two_employee_database(path):
    """2 行 employees 迷你库，供 run_gt 隔离与真实规划集成测试共用。"""
    import duckdb
    with duckdb.connect(str(path)) as connection:
        connection.execute("CREATE TABLE employees(id INTEGER); INSERT INTO employees VALUES (1), (2)")
    return path
