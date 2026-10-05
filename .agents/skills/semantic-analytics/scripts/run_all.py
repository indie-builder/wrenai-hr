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
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from query_execution import (load_env, positive_timeout, regression_questions,
                             regression_directory, run_regression)
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
    try:
        questions = regression_questions(load_questions(questions_path), args.only, args.domain)
        base = Path(args.results).resolve() if args.results else questions_path.parent / "results"
        out = regression_directory(base, questions, args.only, args.domain, args.output_dir, domain_key=True)
    except ValueError as exc:
        sys.exit(str(exc))
    wren = os.environ.get("WREN_BIN", "wren")
    if shutil.which(wren) is None:
        sys.exit(f"未找到 wren CLI: {wren} (设 WREN_BIN 环境变量, 或先 pip install 'wrenai[memory]==0.15.0')")
    try:
        env = load_env(project)
    except ValueError as exc:
        sys.exit(f"项目 .env 无效: {exc}")

    return run_regression(questions, out, env=env, timeout=timeout, tolerance=args.tol,
                          db_file=args.db, project=project, wren=wren)


if __name__ == "__main__":
    sys.exit(main())
