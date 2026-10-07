#!/usr/bin/env python3
"""双路径 SQL 回归。使用已安装包或 export_runner.py 生成的完整目录。

全量写题库同级 results/；子集写其 runs/，失败清理该题 CSV。
WREN_BIN 指定 Wren CLI，默认使用 PATH 上的 wren。
"""
import argparse
import os
from pathlib import Path
import runpy
import shutil
import sys

from hr_analytics.execution import (load_env, positive_timeout, regression_questions,
                             regression_directory, run_regression)
from hr_contracts.tables import numeric_tolerance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", type=Path, required=True, help="题库 questions.py")
    parser.add_argument("--project", type=Path, required=True, help="语义项目目录")
    parser.add_argument("--db", type=Path, required=True, help="只读物理库")
    parser.add_argument("--results", type=Path, help="全量结果目录，默认题库同级 results/")
    parser.add_argument("--output-dir", type=Path, help="本次运行目录")
    parser.add_argument("--tol", type=numeric_tolerance, help="覆盖题库 tolerance，支持零")
    parser.add_argument("--timeout", type=positive_timeout, default=180)
    parser.add_argument("--only", nargs="+")
    parser.add_argument("--domain")
    args = parser.parse_args(argv)
    questions_path, project = args.questions.resolve(), args.project.resolve()
    if not args.db.is_file():
        sys.exit(f"物理库不存在: {args.db}")
    # run_path loads this file even when another questions module is already imported.
    sys.path.insert(0, str(questions_path.parent))
    try:
        questions = runpy.run_path(str(questions_path))["QUESTIONS"]
        questions = regression_questions(questions, args.only, args.domain)
        base = args.results.resolve() if args.results else questions_path.parent / "results"
        out = regression_directory(base, questions, args.only, args.domain, args.output_dir, domain_key=True)
        env = load_env(project)
    except Exception as exc:
        sys.exit(f"题库或项目配置无效: {exc}")
    wren = os.environ.get("WREN_BIN", "wren")
    if shutil.which(wren) is None:
        sys.exit(f"未找到 wren CLI: {wren} (设 WREN_BIN 或安装 wrenai[memory]==0.15.0)")
    return run_regression(questions, out, env=env, timeout=args.timeout, tolerance=args.tol,
                          db_file=args.db, project=project, wren=wren)


if __name__ == "__main__":
    sys.exit(main())
