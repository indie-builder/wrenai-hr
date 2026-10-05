#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双路径 SQL 回归 runner (领域无关 CLI; 进程纪律、错误白名单、证据写入与
逐值比对全部来自共享核心——本目录的 query_execution.py / result_contract.py
链接指向 hr-demo/validation/v2)
- A 路径: gt SQL 经只读 DuckDB worker 执行 (标准答案); B 路径: wren query 经语义层执行 (被测)
- 全量运行写 --results (默认题库同级 results/), 子集运行写其 runs/<子集>/,
  不覆盖全量汇总; 失败题目不写该题 CSV 并移除同名旧文件
用法:
  python3 run_all.py --questions <questions.py> --project <语义项目目录> \
                     --db <duckdb文件> [--results <目录>] [--output-dir <目录>] \
                     [--tol 0.011] [--timeout 180] [--only q01 q02] [--domain <域>]
环境:
  WREN_BIN  wren CLI 路径 (默认取 PATH 上的 wren)
"""
import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from query_execution import (evaluate, load_env, positive_timeout, run_gt, run_wren,
                             write_results, write_summary)
from result_contract import numeric_tolerance

DEFAULT_TIMEOUT = 180


def load_questions(path):
    sys.path.insert(0, str(path.parent))
    try:
        module = __import__(path.stem)
    except Exception as exc:
        sys.exit(f"题库加载失败 {path}: {exc}")
    questions = getattr(module, "QUESTIONS", None)
    if not questions:
        sys.exit(f"题库 {path} 未定义 QUESTIONS 列表")
    return questions


def subset_key(only, domain, questions):
    if domain and not only:
        return domain
    ids = "-".join(question["id"] for question in questions)
    return ids if len(ids) <= 60 else hashlib.sha256(ids.encode()).hexdigest()[:16]


def main():
    parser = argparse.ArgumentParser(description="双路径 SQL 回归 runner")
    parser.add_argument("--questions", required=True, help="题库 questions.py 路径")
    parser.add_argument("--project", required=True, help="语义项目目录 (wren CLI 执行目录)")
    parser.add_argument("--db", required=True, help="A 路径 duckdb 物理库文件 (只读挂载)")
    parser.add_argument("--results", help="全量结果目录 (默认: 题库同级 results/; 子集运行写其 runs/ 子目录)")
    parser.add_argument("--output-dir", help="本次运行输出目录 (覆盖默认 results/runs 布局)")
    parser.add_argument("--tol", type=float, default=None, help="数值容差 (覆盖题库 tolerance; 默认取题库值或 0.011)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单条 SQL 执行超时秒数 (默认 180)")
    parser.add_argument("--only", nargs="+", help="只跑指定题目 id")
    parser.add_argument("--domain", help="只跑指定业务域")
    args = parser.parse_args()
    try:
        timeout = positive_timeout(args.timeout)
    except ValueError:
        sys.exit("--timeout 需为正有限秒数")
    if args.tol is not None:
        try:
            numeric_tolerance(args.tol)
        except ValueError as exc:
            sys.exit(f"--tol 无效: {exc}")
    questions_path = Path(args.questions).resolve()
    project = Path(args.project).resolve()
    if not Path(args.db).exists():
        sys.exit(f"物理库不存在: {args.db}")
    questions = [question for question in load_questions(questions_path)
                 if (not args.only or question["id"] in args.only)
                 and (not args.domain or question.get("domain") == args.domain)]
    if not questions:
        sys.exit("没有匹配的题目")
    if args.output_dir:
        out = Path(args.output_dir).resolve()
    else:
        base = Path(args.results).resolve() if args.results else questions_path.parent / "results"
        subset = args.only is not None or args.domain is not None
        out = base if not subset else base / "runs" / subset_key(args.only, args.domain, questions)
    out.mkdir(parents=True, exist_ok=True)
    wren = os.environ.get("WREN_BIN", "wren")
    if shutil.which(wren) is None:
        sys.exit(f"未找到 wren CLI: {wren} (设 WREN_BIN 环境变量, 或先 pip install 'wrenai[memory]==0.13.4')")
    try:
        env = load_env(project)
    except ValueError as exc:
        sys.exit(f"项目 .env 无效: {exc}")

    results_log = []
    for question in questions:
        qid = question["id"]
        gt = run_gt(question["gt"], db_file=args.db, timeout=timeout)
        execution = run_wren(question["wren"], project=project, wren=wren, env=env, timeout=timeout)
        ok, msg = evaluate(question, gt, execution, args.tol)
        # 失败题目不落 CSV, 并清掉上次运行的同名文件, 避免残留过期"证据"
        write_results(out, qid, {"gt": gt.stdout if ok else None, "wren": execution.stdout if ok else None})
        results_log.append({"id": qid, "domain": question.get("domain", ""), "priority": question.get("priority", ""),
                            "question": question.get("question", ""), "result": "PASS" if ok else "FAIL", "msg": msg})
        print(f"{'✅' if ok else '❌'} {qid:>5} [{question.get('domain','')}|{question.get('priority','')}] {msg}")

    write_summary(out, results_log, ["id", "domain", "priority", "question", "result", "msg"])
    total = len(results_log)
    passed = sum(1 for record in results_log if record["result"] == "PASS")
    p0 = [record for record in results_log if record["priority"] == "P0"]
    print(f"\n===== 汇总: {passed}/{total} PASS | "
          f"P0口径题 {sum(1 for record in p0 if record['result'] == 'PASS')}/{len(p0)} PASS =====")
    fails = [record["id"] for record in results_log if record["result"] == "FAIL"]
    if fails:
        print("失败题目:", ", ".join(fails))
    print(f"报告: {out / 'summary.csv'}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
