#!/usr/bin/env python3
"""固定 SQL 双路径回归；局部运行默认写 runs/<subset>/，不会覆盖全量报告。

python3 hr-demo/validation/v2/run_all.py [--only q03] [--output-dir PATH]
每题可声明 ordered=False、allow_empty=False、tolerance=0.011。
本脚本不会调用模型；NL 生成评测见 eval/nl_eval.py。
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from questions import QUESTIONS

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PROJECT = ROOT / "wren-project"
WREN = ROOT.parent / ".venv/bin/wren"
VENV_PY = ROOT.parent / ".venv/bin/python"
DUCKDB_FILE = ROOT / "db/duckdb/public.duckdb"
DUCKDB_WORKER = ROOT.parent / "hr_query/duckdb_worker.py"
NUM_TOL = 0.011


@dataclass
class Execution:
    stdout: str = ""
    returncode: int | None = None
    status: str = "ok"
    # Raw stderr is deliberately not retained: driver exceptions can include credentials.
    stderr_present: bool = False
    error_type: str | None = None

    @property
    def ok(self):
        return self.status == "ok" and self.returncode == 0

    def trace(self):
        return {"status": self.status, "returncode": self.returncode,
                "stderr_present": self.stderr_present, "error_type": self.error_type}

    def message(self):
        detail = f", {self.error_type}" if self.error_type else ""
        return f"{self.status} (exit={self.returncode}{detail})"


def load_env(project=PROJECT, base=None):
    """Optional literal dotenv; no shell/interpolation, existing environment wins.

    Only connection-variable names are imported. Runtime/search-path variables must
    be set by the caller, never by a project .env. Invalid lines fail without values.
    """
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
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].lstrip().startswith("#")):
                raise ValueError(f".env 第 {number} 行引号无效（内容已隐藏）")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        env.setdefault(key, value)
    return env


def run_process(argv, *, cwd=None, env=None, timeout=180, input_text=None):
    """Never use a shell; a partial stdout on failure is not a query result."""
    try:
        proc = subprocess.run([str(x) for x in argv], cwd=cwd, env=env,
                              input=input_text, capture_output=True, text=True,
                              encoding="utf-8", errors="strict", timeout=timeout)
    except subprocess.TimeoutExpired:
        return Execution(status="timeout")
    except (OSError, UnicodeError):
        return Execution(status="launch_or_encoding_error")
    # Worker emits one class name. Never persist arbitrary stderr from Wren/driver.
    safe_types = {"BinderException", "CatalogException", "ParserException", "IOException",
                  "OutOfMemoryException", "PermissionException", "InvalidInputException",
                  "ConversionException", "ValueError", "RowLimitExceeded", "KeyError", "TypeError",
                  "ModuleNotFoundError", "Error"}
    error_type = proc.stderr.strip() if proc.stderr.strip() in safe_types else None
    return Execution(stdout=proc.stdout if proc.returncode == 0 else "",
                     returncode=proc.returncode,
                     status="ok" if proc.returncode == 0 else "process_error",
                     stderr_present=bool(proc.stderr.strip()), error_type=error_type)


def table_csv(columns, rows):
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows([["" if value is None else value for value in row] for row in rows])
    return buf.getvalue()


def run_gt(sql, *, db_file=None, python=None, timeout=180):
    db_file = Path(db_file or DUCKDB_FILE)
    if not db_file.is_file():
        return Execution(status="missing_database")
    py = python or (VENV_PY if VENV_PY.exists() else sys.executable)
    execution = run_process([py, DUCKDB_WORKER], timeout=timeout,
                            input_text=json.dumps({"database": str(db_file), "sql": sql}))
    if not execution.ok:
        return execution
    try:
        payload = json.loads(execution.stdout)
        if (payload.get("complete") is not True or not isinstance(payload.get("columns"), list)
                or not payload["columns"] or not isinstance(payload.get("rows"), list)
                or not all(isinstance(c, str) and c for c in payload["columns"])
                or not all(isinstance(r, list) and len(r) == len(payload["columns"])
                           and all(c is None or isinstance(c, str) for c in r)
                           for r in payload["rows"])):
            raise ValueError("invalid envelope")
        execution.stdout = table_csv(payload["columns"], payload["rows"])
    except (ValueError, TypeError, AttributeError):
        execution.status, execution.stdout = "invalid_output", ""
    return execution


def run_wren(sql, *, project=None, wren=None, env=None, timeout=180):
    project = Path(project or PROJECT)
    execution = run_process([wren or WREN, "query", "--sql", sql, "-o", "csv", "-q"],
                            cwd=project, env=load_env(project) if env is None else env,
                            timeout=timeout)
    if execution.ok:
        try:
            parse_csv(execution.stdout)
        except ValueError:
            execution.status, execution.stdout = "invalid_output", ""
    return execution


def parse_csv(text):
    if not text or not text.endswith(("\n", "\r")):
        raise ValueError("CSV输出缺失或不完整")
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise ValueError("CSV格式错误") from exc
    # Wren 0.13.4 typer.echo adds a final blank line to pandas' CSV.
    while rows and rows[-1] == []:
        rows.pop()
    if not rows or not rows[0] or any(not h.strip() for h in rows[0]):
        raise ValueError("CSV表头缺失")
    headers = [h.strip() for h in rows[0]]
    if len(headers) != len(set(headers)):
        raise ValueError("CSV列名重复")
    if any(len(row) != len(headers) for row in rows[1:]):
        raise ValueError("CSV行列数不完整")
    return headers, rows[1:]


def norm_cell(cell):
    value = "" if cell is None else str(cell).strip()
    if value in ("", "NULL", "None"):
        return ""
    if value.lower() in ("t", "true"):
        return "true"
    if value.lower() in ("f", "false"):
        return "false"
    return value


def cell_key(cell):
    value = norm_cell(cell)
    try:
        number = Decimal(value)
    except InvalidOperation:
        return ("s", value)
    return ("n", number)


def numeric_tolerance(value):
    try:
        tolerance = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("tolerance必须为非负有限数值") from exc
    if not tolerance.is_finite() or tolerance < 0:
        raise ValueError("tolerance必须为非负有限数值")
    return tolerance


def rows_equal(left, right, tolerance=NUM_TOL):
    tol = numeric_tolerance(tolerance)
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        ka, kb = cell_key(a), cell_key(b)
        if ka[0] != kb[0]:
            return False
        if ka[0] == "n":
            # NaN/Infinity represent an invalid metric, even on both paths.
            if not ka[1].is_finite() or not kb[1].is_finite() or abs(ka[1] - kb[1]) > tol:
                return False
        elif ka[1] != kb[1]:
            return False
    return True


def compare(gt_text, wren_text, *, ordered=False, allow_empty=False, tolerance=NUM_TOL):
    tol = numeric_tolerance(tolerance)
    if not isinstance(ordered, bool) or not isinstance(allow_empty, bool):
        raise ValueError("ordered和allow_empty必须为bool")
    try:
        gh, gr = parse_csv(gt_text)
        wh, wr = parse_csv(wren_text)
    except ValueError as exc:
        return False, str(exc), []
    if gh != wh:
        return False, f"列名不一致 gt={gh} wren={wh}", gh
    if len(gr) != len(wr):
        return False, f"行数不一致 gt={len(gr)} wren={len(wr)}", gh
    if not gr:
        return (True, "OK (允许空结果)", gh) if allow_empty else (False, "空结果", gh)
    if ordered:
        for index, (left, right) in enumerate(zip(gr, wr), 1):
            if not rows_equal(left, right, tol):
                return False, f"第{index}行不一致（保留原始顺序）", gh
    else:
        # Bag matching preserves duplicate rows and tolerances. Lexical sorting
        # alone mispairs values such as 9.999/10.001; greedy matching can also fail.
        candidates = [[j for j, row in enumerate(wr) if rows_equal(left, row, tol)] for left in gr]
        assigned = {}

        def match(index, seen):
            for j in candidates[index]:
                if j in seen:
                    continue
                seen.add(j)
                if j not in assigned or match(assigned[j], seen):
                    assigned[j] = index
                    return True
            return False

        for index in sorted(range(len(gr)), key=lambda i: len(candidates[i])):
            if not match(index, set()):
                return False, f"第{index + 1}行无等价匹配（含重复行计数）", gh
    return True, "OK", gh


def comparison_options(question):
    return {key: question.get(key, default) for key, default in
            (("ordered", False), ("allow_empty", False), ("tolerance", NUM_TOL))}


def select_questions(questions, only=None, domain=None):
    if only is not None:
        unknown = set(only) - {q["id"] for q in questions}
        if unknown:
            raise ValueError("未知题号: " + ", ".join(sorted(unknown)))
    selected = [q for q in questions if (only is None or q["id"] in only)
                and (domain is None or q["domain"] == domain)]
    if not selected:
        raise ValueError("没有匹配的题目")
    return selected


def output_directory(questions, *, subset=False, output=None):
    if output:
        directory = Path(output).resolve()
        if subset and directory == HERE.resolve():
            raise ValueError("局部运行不能写入全量报告目录；请使用独立--output-dir")
        return directory
    if not subset:
        return HERE
    ids = "-".join(q["id"] for q in questions)
    key = ids if len(ids) < 80 else hashlib.sha256(ids.encode()).hexdigest()[:16]
    return HERE / "runs" / key


def write_summary(directory, records, fields):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+")
    parser.add_argument("--domain")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("timeout必须为正有限秒数")
        questions = select_questions(QUESTIONS, args.only, args.domain)
        directory = output_directory(questions, subset=args.only is not None or args.domain is not None,
                                     output=args.output_dir)
        env = load_env()
    except (ValueError, OSError) as exc:
        # OSError text can expose paths but never values; dotenv details remain masked.
        parser.error(str(exc))
    results_dir = directory / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for question in questions:
        qid = question["id"]
        gt = run_gt(question["gt"], timeout=args.timeout)
        wren = run_wren(question["wren"], env=env, timeout=args.timeout)
        for name, execution in (("gt", gt), ("wren", wren)):
            path = results_dir / f"{qid}.{name}.csv"
            path.unlink(missing_ok=True)  # remove stale successes after a failed rerun
            if execution.ok:
                path.write_text(execution.stdout, encoding="utf-8")
        if not gt.ok or not wren.ok:
            ok = False
            message = f"执行失败 GT={gt.message()} Wren={wren.message()}"
        else:
            try:
                ok, message, _ = compare(gt.stdout, wren.stdout, **comparison_options(question))
            except ValueError as exc:
                ok, message = False, f"题库元数据错误: {exc}"
        record = {**{key: question[key] for key in ("id", "domain", "priority", "question")},
                  "result": "PASS" if ok else "FAIL", "msg": message}
        records.append(record)
        (results_dir / f"{qid}.execution.json").write_text(
            json.dumps({"gt": gt.trace(), "wren": wren.trace()}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{record['result']} {qid} [{question['domain']}] {message}")
    write_summary(directory, records, ["id", "domain", "priority", "question", "result", "msg"])
    passed = sum(r["result"] == "PASS" for r in records)
    print(f"{passed}/{len(records)} PASS；报告: {directory / 'summary.csv'}")
    return 0 if passed == len(records) else 1


if __name__ == "__main__":
    sys.exit(main())
