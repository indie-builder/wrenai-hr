#!/usr/bin/env python3
"""Offline NL→SQL evaluation: export an answer-free package, then score real JSONL.

Never calls a model or labels fixed SQL replay as NL generation. Required record
fields: id, original question, generated_sql, context_refs; optional model and
run_metadata. Raw SQL/results are retained; display precision affects comparison
copies only. Missing generation, policy, planning, execution and answer failures
remain separate outcomes. Provenance of submitted records belongs to the operator.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_all as regression
from hr_query.sql_policy import PolicyError, mdl_tables, validate_sql
from reports import digest, write_json, write_results, write_summary
from result_contract import compare_tables
from eval_protocol import export_package, load_records, normalize_display, output_schema


def compare_answer(gt, actual, question):
    # Aliases are presentation; public column positions and precision are binding.
    gh, gr = regression.parse_csv(gt)
    ah, ar = regression.parse_csv(actual)
    schema = output_schema(question)
    if len(gh) != len(ah) or len(gh) != len(schema):
        return False, "列数不符合公开output_schema", gh
    return compare_tables(gh, normalize_display(gr, schema), gh, normalize_display(ar, schema),
                          **regression.comparison_options(question))


def evaluate_question(question, record, error, *, manifest, project, mdl, database, timeout):
    trace = {"id": question["id"], "question": question["question"],
             "execution_path": "wren dry-plan -> AST policy -> restricted read-only DuckDB query",
             "comparison": {**regression.comparison_options(question), "column_aliases": "ignored_by_position",
                            "output_schema": output_schema(question), "rounding": "ROUND_HALF_UP"},
             "generation_record": record}
    if error:
        return "INVALID_GENERATION", error, trace, {}
    if not record or not record.get("generated_sql", "") or not record["generated_sql"].strip():
        return "NOT_GENERATED", "没有生成SQL", trace, {}
    semantic, physical = mdl_tables(manifest)
    try:
        sql = validate_sql(record["generated_sql"], semantic)
    except PolicyError as exc:
        return "SQL_REJECTED", str(exc), trace, {}
    plan = regression.run_process([regression.WREN, "dry-plan", "--sql", sql,
                                   "--datasource", "duckdb", "--mdl", mdl], cwd=project, timeout=timeout)
    trace["dry_plan"] = plan.trace()
    if not plan.ok:
        return "PLAN_FAILED", plan.message(), trace, {}
    try:
        planned_sql = validate_sql(plan.stdout.strip(), physical, physical=True)
    except PolicyError as exc:
        return "PLAN_REJECTED", str(exc), trace, {}
    trace["planned_sql"] = planned_sql
    actual = regression.run_gt(planned_sql, db_file=database, timeout=timeout)
    trace["query"] = actual.trace()
    if not actual.ok:
        return "EXECUTION_FAILED", actual.message(), trace, {}
    gt = regression.run_gt(question["gt"], db_file=database, timeout=timeout)
    trace["ground_truth"] = gt.trace()
    outputs = {"generated": actual.stdout}
    if not gt.ok:
        return "GT_FAILED", gt.message(), trace, outputs
    outputs["gt"] = gt.stdout
    try:
        ok, message, _ = compare_answer(gt.stdout, actual.stdout, question)
    except ValueError as exc:
        return "INVALID_OUTPUT", str(exc), trace, outputs
    return ("PASS" if ok else "RESULT_DIFFERENCE"), message, trace, outputs


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
    return 0 if counts["PASS"] == len(questions) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "run"):
        command = sub.add_parser(name)
        command.add_argument("--output-dir", type=Path, required=True, help="必须为新目录")
        command.add_argument("--project", type=Path, default=regression.PROJECT)
        command.add_argument("--only", nargs="+")
        command.add_argument("--domain")
        if name == "run":
            command.add_argument("--records", type=Path, required=True)
            command.add_argument("--database", type=Path, default=regression.DUCKDB_FILE)
            command.add_argument("--timeout", type=regression.positive_timeout, default=180)
    args = parser.parse_args(argv)
    try:
        questions = regression.select_questions(regression.QUESTIONS, args.only, args.domain)
        if args.command == "export":
            export_package(args.output_dir, questions, args.project.resolve())
            print(f"已导出{len(questions)}题；未生成SQL。")
            return 0
        return run_evaluation(args.output_dir, questions, args.records, args.project.resolve(),
                              args.database.resolve(), args.timeout)
    except (OSError, ValueError) as exc:
        print(f"评测输入/配置错误: {type(exc).__name__}；请检查路径、JSONL协议和目录是否已存在。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
