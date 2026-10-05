"""Restricted query processes, execution evidence and scoring; no DuckDB import here.

Also the shared home for regression IO helpers: the skill runner reaches this
module through a sibling symlink, so evidence writers must live beside Execution.
"""
import csv
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from result_contract import compare, comparison_options, parse_csv, table_csv

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "wren-project"
WREN = ROOT.parent / ".venv/bin/wren"
if not WREN.is_file():  # worktree 等缺仓库内 venv 时回退 PATH 上的 wren
    WREN = shutil.which("wren") or WREN
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


def evaluate(question, gt, wren, tolerance=None):
    """按题库契约给两次执行打分；tolerance 非 None 时覆盖题库声明值。"""
    if not gt.ok or not wren.ok:
        return False, f"执行失败 GT={gt.message()} Wren={wren.message()}"
    try:
        options = comparison_options(question)
        if tolerance is not None:
            options["tolerance"] = tolerance
        ok, message, _ = compare(gt.stdout, wren.stdout, **options)
        return ok, message
    except ValueError as exc:
        return False, f"题库元数据错误: {exc}"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_results(directory, qid, outputs):
    """Reruns never leave stale successful query CSVs."""
    for name, text in outputs.items():
        path = directory / f"{qid}.{name}.csv"
        path.unlink(missing_ok=True)
        if text is not None:
            path.write_text(text, encoding="utf-8")


def write_summary(directory, records, fields):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def positive_timeout(value):
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout必须为正有限秒数")
    return timeout


def regression_questions(questions, only=None, domain=None):
    if only is not None and (unknown := set(only) - {q["id"] for q in questions}):
        raise ValueError("未知题号: " + ", ".join(sorted(unknown)))
    selected = [q for q in questions if (only is None or q["id"] in only)
                and (domain is None or q.get("domain") == domain)]
    if not selected:
        raise ValueError("没有匹配的题目")
    return selected


def regression_directory(base, questions, only=None, domain=None, output=None, *, domain_key=False):
    subset = only is not None or domain is not None
    if output:
        directory = Path(output).resolve()
        if subset and directory == base.resolve():
            raise ValueError("局部运行不能写入全量报告目录；请使用独立--output-dir")
        return directory
    if not subset:
        return base
    key = domain if domain_key and domain and not only else "-".join(q["id"] for q in questions)
    limit = 61 if domain_key else 80
    if not (domain_key and domain and not only) and len(key) >= limit:
        key = hashlib.sha256(key.encode()).hexdigest()[:16]
    return base / "runs" / key


def run_regression(questions, directory, *, env, timeout=180, tolerance=None,
                   db_file=None, project=None, wren=None, execution_evidence=False):
    results = directory / "results" if execution_evidence else directory
    results.mkdir(parents=True, exist_ok=True)
    records = []
    fields = ["id", "domain", "priority", "question", "result", "msg"]
    for question in questions:
        qid = question["id"]
        gt = run_gt(question["gt"], db_file=db_file, timeout=timeout)
        semantic = run_wren(question["wren"], project=project, wren=wren, env=env, timeout=timeout)
        ok, message = evaluate(question, gt, semantic, tolerance)
        write_results(results, qid, {name: result.stdout if (result.ok if execution_evidence else ok) else None
                                    for name, result in (("gt", gt), ("wren", semantic))})
        record = {**{key: question.get(key, "") for key in fields[:4]},
                  "result": "PASS" if ok else "FAIL", "msg": message}
        records.append(record)
        if execution_evidence:
            write_json(results / f"{qid}.execution.json", {"gt": gt.trace(), "wren": semantic.trace()})
        print(f"{record['result']} {qid} [{record['domain']}] {message}")
    write_summary(directory, records, fields)
    passed = sum(record["result"] == "PASS" for record in records)
    p0 = [record for record in records if record["priority"] == "P0"]
    print(f"{passed}/{len(records)} PASS；P0口径题 {sum(r['result'] == 'PASS' for r in p0)}/{len(p0)} PASS")
    if failures := [r["id"] for r in records if r["result"] == "FAIL"]:
        print("失败题目:", ", ".join(failures))
    print(f"报告: {directory / 'summary.csv'}")
    return int(passed != len(records))
