#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双路径 SQL 回归 runner (领域无关泛化版)
- A 路径: gt SQL 经 duckdb 模块只读挂载物理库 (标准答案)
- B 路径: wren query 经语义层执行 (被测)
- 逐值比对 (Decimal 数值容差并拒绝 NaN/Inf, 布尔/空值归一; 无序题多重集匹配, ordered=True
  逐行保序; 空结果默认判失败, allow_empty=True 放行) → qXX.{gt,wren}.csv + summary.csv
- 题级可声明 ordered / allow_empty / tolerance (默认 False/False/0.011), --tol 覆盖题库 tolerance
- 执行失败 (子进程非零退出或超时) 记该题 FAIL 并继续; summary 只记错误类别, 不写入异常原文
- 输出纪律: 全量运行 summary.csv 写结果目录 (默认题库同级 results/), 子集运行写其 runs/<子集>/
  不覆盖全量汇总 (--output-dir 可整体覆盖); 失败题目不写该题 CSV 并移除同名旧文件
用法:
  python3 run_all.py --questions <questions.py> --project <语义项目目录> \
                     --db <duckdb文件> [--results <目录>] [--output-dir <目录>] \
                     [--tol 0.011] [--timeout 180] [--only q01 q02] [--domain <域>]
环境:
  WREN_BIN  wren CLI 路径 (默认取 PATH 上的 wren)
"""
import argparse, csv, hashlib, math, os, re, shutil, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from result_contract import (NUM_TOL as DEFAULT_TOL, compare as compare_results,
                             norm_cell, numeric_tolerance, parse_csv, rows_equal)
DEFAULT_TIMEOUT = 180
# 可写入 summary 的错误类别白名单; 其余 stderr 一律归类, 避免异常原文 (可能含连接串) 落盘
SAFE_ERROR_TYPES = {
    "BinderException", "CatalogException", "ParserException", "IOException",
    "OutOfMemoryException", "PermissionException", "InvalidInputException",
    "ConversionException", "ValueError", "RowLimitExceeded", "KeyError",
    "TypeError", "ModuleNotFoundError", "Error",
}

def load_questions(path: Path):
    sys.path.insert(0, str(path.parent))
    try:
        mod = __import__(path.stem)
    except Exception as e:
        sys.exit(f"题库加载失败 {path}: {e}")
    qs = getattr(mod, "QUESTIONS", None)
    if not qs:
        sys.exit(f"题库 {path} 未定义 QUESTIONS 列表")
    return qs

def load_env(project: Path):
    """合并进程环境与项目 .env: 仅填充缺失键, 不覆盖已有变量; 坏行跳过并告警。"""
    env = dict(os.environ)
    env_file = project / ".env"
    if not env_file.exists():
        return env
    for number, raw in enumerate(env_file.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            print(f"警告: 跳过 .env 第 {number} 行 (格式无效)", file=sys.stderr)
            continue
        env.setdefault(key, value.strip())
    return env

def run_process(command, *, timeout, **options):
    """Return complete process output, or an explicit timeout without partial evidence."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, **options)
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "", None


def run_gt_duckdb(sql: str, db: str, timeout: float):
    """duckdb 只读挂载执行 gt SQL。返回 (stdout, stderr, returncode); returncode=None 表示超时。"""
    code = (
        "import sys, csv, duckdb\n"
        f"con = duckdb.connect({db!r}, read_only=True)\n"
        "try:\n"
        "    cur = con.execute(sys.argv[1])\n"
        "    w = csv.writer(sys.stdout)\n"
        "    w.writerow([d[0] for d in cur.description])\n"
        "    w.writerows(cur.fetchall())\n"
        "finally:\n"
        "    con.close()\n"
    )
    return run_process([sys.executable, "-c", code, sql], timeout=timeout)

def run_wren(sql: str, wren: str, project: Path, env, timeout: float):
    """wren query 经语义层执行。返回 (stdout, stderr, returncode); returncode=None 表示超时。"""
    return run_process([wren, "query", "--sql", sql, "-o", "csv", "-q"],
                       timeout=timeout, cwd=str(project), env=env)

def compare(gt_text, wren_text, **options):
    # Complete CSV is required; a header-only result can explicitly allow no rows.
    return compare_results(gt_text, wren_text, **options)[:2]

def last_stderr_line(stderr_text):
    lines = (stderr_text or "").strip().splitlines()
    return lines[-1].strip() if lines else ""

def evaluate(question, outputs, timeout, tolerance=None):
    for side, (text, stderr, rc) in outputs.items():
        if rc is None:
            return False, f"{side}执行超时(>{timeout:g}s)"
        if rc:
            last = last_stderr_line(stderr)
            return False, f"{side}执行失败: {last if last in SAFE_ERROR_TYPES else f'exit={rc}'}"
    try:
        return compare(outputs["GT"][0], outputs["Wren"][0],
                       ordered=question.get("ordered", False), allow_empty=question.get("allow_empty", False),
                       tolerance=question.get("tolerance", DEFAULT_TOL) if tolerance is None else tolerance)
    except ValueError as exc:
        return False, f"题库元数据错误: {exc}"

def subset_key(args, qs):
    if args.domain and not args.only:
        return args.domain
    ids = "-".join(q["id"] for q in qs)
    return ids if len(ids) <= 60 else hashlib.sha256(ids.encode()).hexdigest()[:16]

def main():
    ap = argparse.ArgumentParser(description="双路径 SQL 回归 runner")
    ap.add_argument("--questions", required=True, help="题库 questions.py 路径")
    ap.add_argument("--project", required=True, help="语义项目目录 (wren CLI 执行目录)")
    ap.add_argument("--db", required=True, help="A 路径 duckdb 物理库文件 (只读挂载)")
    ap.add_argument("--results", help="全量结果目录 (默认: 题库同级 results/; 子集运行写其 runs/ 子目录)")
    ap.add_argument("--output-dir", help="本次运行输出目录 (覆盖默认 results/runs 布局)")
    ap.add_argument("--tol", type=float, default=None, help="数值容差 (覆盖题库 tolerance; 默认取题库值或 0.011)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单条 SQL 执行超时秒数 (默认 180)")
    ap.add_argument("--only", nargs="+", help="只跑指定题目 id")
    ap.add_argument("--domain", help="只跑指定业务域")
    args = ap.parse_args()

    if not math.isfinite(args.timeout) or args.timeout <= 0:
        sys.exit("--timeout 需为正有限秒数")
    if args.tol is not None:
        try:
            numeric_tolerance(args.tol)
        except ValueError as e:
            sys.exit(f"--tol 无效: {e}")
    qpath = Path(args.questions).resolve()
    project = Path(args.project).resolve()
    if not Path(args.db).exists():
        sys.exit(f"物理库不存在: {args.db}")
    qs = [q for q in load_questions(qpath)
          if (not args.only or q["id"] in args.only)
          and (not args.domain or q.get("domain") == args.domain)]
    if not qs:
        sys.exit("没有匹配的题目")
    subset = args.only is not None or args.domain is not None
    if args.output_dir:
        out = Path(args.output_dir).resolve()
    else:
        base = Path(args.results).resolve() if args.results else qpath.parent / "results"
        out = base if not subset else base / "runs" / subset_key(args, qs)
    out.mkdir(parents=True, exist_ok=True)
    wren = os.environ.get("WREN_BIN", "wren")
    if shutil.which(wren) is None:
        sys.exit(f"未找到 wren CLI: {wren} (设 WREN_BIN 环境变量, 或先 pip install 'wrenai[memory]==0.13.4')")

    env = load_env(project)
    db_path = str(Path(args.db).resolve())
    results_log = []
    for q in qs:
        qid = q["id"]
        outputs = {"GT": run_gt_duckdb(q["gt"], db_path, args.timeout),
                   "Wren": run_wren(q["wren"], wren, project, env, args.timeout)}
        ok, msg = evaluate(q, outputs, args.timeout, args.tol)
        # 失败题目不落 CSV, 并清掉上次运行的同名文件, 避免残留过期"证据"
        for label, (text, _, _) in outputs.items():
            side = label.lower()
            path = out / f"{qid}.{side}.csv"
            if ok:
                path.write_text(text, encoding="utf-8")
            else:
                path.unlink(missing_ok=True)
        results_log.append({"id": qid, "domain": q.get("domain", ""), "priority": q.get("priority", ""),
                            "question": q.get("question", ""), "result": "PASS" if ok else "FAIL",
                            "msg": msg})
        print(f"{'✅' if ok else '❌'} {qid:>5} [{q.get('domain','')}|{q.get('priority','')}] {msg}")

    with open(out / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "domain", "priority", "question", "result", "msg"])
        for r in results_log:
            w.writerow([r["id"], r["domain"], r["priority"], r["question"], r["result"], r["msg"]])

    total = len(results_log)
    passed = sum(1 for r in results_log if r["result"] == "PASS")
    p0 = [r for r in results_log if r["priority"] == "P0"]
    p0_pass = sum(1 for r in p0 if r["result"] == "PASS")
    print(f"\n===== 汇总: {passed}/{total} PASS | P0口径题 {p0_pass}/{len(p0)} PASS =====")
    fails = [r for r in results_log if r["result"] == "FAIL"]
    if fails:
        print("失败题目:", ", ".join(r["id"] for r in fails))
    print(f"报告: {out / 'summary.csv'}")
    sys.exit(0 if passed == total else 1)

if __name__ == "__main__":
    main()
