#!/usr/bin/env python3
"""Export answer-free NL questions and score submitted JSONL with independent GT.

Raw SQL/results and failure stages are retained. Display precision applies only
inside comparison. This runner never generates SQL or measures live model accuracy.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hr_query.sql_policy import PolicyError, mdl_tables, validate_sql
from questions import QUESTIONS
from query_execution import (DUCKDB_FILE, PROJECT, WREN, digest, positive_timeout, regression_questions,
                             run_gt, run_process, write_json, write_results, write_summary)
from result_contract import compare, comparison_options
from eval_protocol import export_package, load_records, output_schema


def evaluate_question(question, record, error, *, manifest, project, mdl, database, timeout):
    trace = {"id": question["id"], "question": question["question"],
             "execution_path": "wren dry-plan -> AST policy -> restricted read-only DuckDB query",
             "comparison": {**comparison_options(question), "column_aliases": "ignored_by_position",
                            "output_schema": output_schema(question), "rounding": "ROUND_HALF_UP"},
             "generation_record": record}
    if error:
        return "INVALID_GENERATION", error, trace, {}
    if not record or not (record["generated_sql"] or "").strip():
        return "NOT_GENERATED", "没有生成SQL", trace, {}
    semantic, physical = mdl_tables(manifest)
    try:
        sql = validate_sql(record["generated_sql"], semantic)
    except PolicyError as exc:
        return "SQL_REJECTED", str(exc), trace, {}
    plan = run_process([WREN, "dry-plan", "--sql", sql,
                        "--datasource", "duckdb", "--mdl", mdl], cwd=project, timeout=timeout)
    trace["dry_plan"] = plan.trace()
    if not plan.ok:
        return "PLAN_FAILED", plan.message(), trace, {}
    try:
        planned_sql = validate_sql(plan.stdout.strip(), physical, physical=True)
    except PolicyError as exc:
        return "PLAN_REJECTED", str(exc), trace, {}
    trace["planned_sql"] = planned_sql
    actual = run_gt(planned_sql, db_file=database, timeout=timeout)
    trace["query"] = actual.trace()
    if not actual.ok:
        return "EXECUTION_FAILED", actual.message(), trace, {}
    gt = run_gt(question["gt"], db_file=database, timeout=timeout)
    trace["ground_truth"] = gt.trace()
    outputs = {"generated": actual.stdout}
    if not gt.ok:
        return "GT_FAILED", gt.message(), trace, outputs
    outputs["gt"] = gt.stdout
    ok, message, columns = compare(gt.stdout, actual.stdout, output_schema=trace["comparison"]["output_schema"],
                                   **comparison_options(question))
    status = "INVALID_OUTPUT" if not columns else "PASS" if ok else "RESULT_DIFFERENCE"
    return status, message, trace, outputs


def run_evaluation(directory, questions, records_path, project, database, timeout):
    records, errors = load_records(records_path, questions)
    directory.mkdir(parents=True, exist_ok=False)
    traces, results = directory / "trace", directory / "results"
    traces.mkdir()
    results.mkdir()
    mdl = project / "target/mdl.json"
    manifest = json.loads(mdl.read_text(encoding="utf-8"))
    summary = []
    for question in questions:
        qid = question["id"]
        status, message, trace, outputs = evaluate_question(
            question, records.get(qid), errors.get(qid), manifest=manifest, project=project,
            mdl=mdl, database=database, timeout=timeout)
        trace.update({"status": status, "message": message})
        write_json(traces / f"{qid}.json", trace)
        write_results(results, qid, outputs)
        summary.append({"id": qid, "question": question["question"], "status": status, "message": message})
        print(f"{qid}: {status}")
    write_summary(directory, summary, ["id", "question", "status", "message"])
    counts = Counter(item["status"] for item in summary)
    write_json(directory / "run.json", {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(), "counts": dict(counts),
        "total_questions": len(questions), "passed": counts["PASS"],
        "pass_rate_all_questions": counts["PASS"] / len(questions),
        "records_sha256": digest(records_path), "mdl_sha256": digest(mdl),
        "generation_performed_by_this_runner": False,
        "note": "仅评价提交记录；NOT_GENERATED包含在总分母，不代表实时模型准确率。",
    })
    return int(counts["PASS"] != len(questions))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "run"):
        command = sub.add_parser(name)
        command.add_argument("--output-dir", type=Path, required=True, help="必须为新目录")
        command.add_argument("--project", type=Path, default=PROJECT)
        command.add_argument("--only", nargs="+")
        command.add_argument("--domain")
        if name == "run":
            command.add_argument("--records", type=Path, required=True)
            command.add_argument("--database", type=Path, default=DUCKDB_FILE)
            command.add_argument("--timeout", type=positive_timeout, default=180)
    args = parser.parse_args(argv)
    try:
        questions = regression_questions(QUESTIONS, args.only, args.domain)
        if args.command == "export":
            export_package(args.output_dir, questions, args.project.resolve())
            print(f"已导出{len(questions)}题；未生成SQL。")
            return 0
        return run_evaluation(args.output_dir, questions, args.records, args.project.resolve(),
                              args.database.resolve(), args.timeout)
    except (OSError, ValueError) as exc:
        print(f"评测输入/配置错误: {exc}；请检查路径、JSONL协议和目录是否已存在。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
