"""Restricted query processes and safe execution evidence; no DuckDB import here."""
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

from result_contract import parse_csv, table_csv

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "wren-project"
WREN = ROOT.parent / ".venv/bin/wren"
VENV_PY = ROOT.parent / ".venv/bin/python"
DUCKDB_FILE = ROOT / "db/duckdb/public.duckdb"
DUCKDB_WORKER = ROOT.parent / "hr_query/duckdb_worker.py"
SAFE_ERRORS = {"BinderException", "CatalogException", "ParserException", "IOException",
               "OutOfMemoryException", "PermissionException", "InvalidInputException",
               "ConversionException", "ValueError", "RowLimitExceeded", "KeyError", "TypeError",
               "ModuleNotFoundError", "Error"}


@dataclass
class Execution:
    stdout: str = ""
    returncode: int | None = None
    status: str = "ok"
    stderr_present: bool = False
    error_type: str | None = None

    @property
    def ok(self):
        return self.status == "ok" and self.returncode == 0

    def trace(self):
        return {key: getattr(self, key) for key in
                ("status", "returncode", "stderr_present", "error_type")}

    def message(self):
        detail = f", {self.error_type}" if self.error_type else ""
        return f"{self.status} (exit={self.returncode}{detail})"


def load_env(project=PROJECT, base=None):
    """Literal dotenv; existing values win, and project files cannot alter runtime paths."""
    env = dict(os.environ if base is None else base)
    path = Path(project) / ".env"
    if not path.exists():
        return env
    protected = {"PATH", "HOME", "SHELL", "ENV", "BASH_ENV", "ZDOTDIR", "WREN_HOME"}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f".env 第 {number} 行格式无效（内容已隐藏）")
        if key in protected or key.startswith(("LD_", "DYLD_", "PYTHON")):
            raise ValueError(f".env 第 {number} 行包含运行时配置（内容已隐藏）")
        if value.startswith(("'", '"')):
            end = value.find(value[0], 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].lstrip().startswith("#")):
                raise ValueError(f".env 第 {number} 行引号无效（内容已隐藏）")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        env.setdefault(key, value)
    return env


def run_process(argv, *, cwd=None, env=None, timeout=180, input_text=None):
    """No shell, partial stdout or arbitrary driver stderr reaches the report."""
    try:
        process = subprocess.run([str(value) for value in argv], cwd=cwd, env=env,
                                 input=input_text, capture_output=True, text=True,
                                 encoding="utf-8", errors="strict", timeout=timeout)
    except subprocess.TimeoutExpired:
        return Execution(status="timeout")
    except (OSError, UnicodeError):
        return Execution(status="launch_or_encoding_error")
    error = process.stderr.strip()
    return Execution(stdout=process.stdout if process.returncode == 0 else "",
                     returncode=process.returncode,
                     status="ok" if process.returncode == 0 else "process_error",
                     stderr_present=bool(error), error_type=error if error in SAFE_ERRORS else None)


def run_gt(sql, *, db_file=None, python=None, timeout=180):
    database = Path(db_file or DUCKDB_FILE)
    if not database.is_file():
        return Execution(status="missing_database")
    execution = run_process([python or (VENV_PY if VENV_PY.exists() else sys.executable), DUCKDB_WORKER],
                            timeout=timeout, input_text=json.dumps({"database": str(database), "sql": sql}))
    if execution.ok:
        try:
            payload = json.loads(execution.stdout)
            columns, rows = payload["columns"], payload["rows"]
            if (payload.get("complete") is not True or not isinstance(columns, list) or not columns
                    or not all(isinstance(column, str) and column for column in columns)
                    or not isinstance(rows, list)
                    or not all(isinstance(row, list) and len(row) == len(columns)
                               and all(cell is None or isinstance(cell, str) for cell in row) for row in rows)):
                raise ValueError("invalid envelope")
            execution.stdout = table_csv(columns, rows)
            parse_csv(execution.stdout)
        except (ValueError, TypeError, AttributeError, KeyError):
            execution.status, execution.stdout = "invalid_output", ""
    return execution


def run_wren(sql, *, project=None, wren=None, env=None, timeout=180):
    project = Path(project or PROJECT)
    execution = run_process([wren or WREN, "query", "--sql", sql, "-o", "csv", "-q"],
                            cwd=project, env=load_env(project) if env is None else env, timeout=timeout)
    if execution.ok:
        try:
            parse_csv(execution.stdout)
        except ValueError:
            execution.status, execution.stdout = "invalid_output", ""
    return execution


def positive_timeout(value):
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout必须为正有限秒数")
    return timeout
