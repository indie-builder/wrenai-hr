#!/usr/bin/env python3
"""Provider-neutral, offline NL→SQL evaluation. Never calls an LLM.

Export an answer-free package for a real agent:
  .venv/bin/python hr-delivery/validation/v2/eval/nl_eval.py export --output-dir /tmp/hr-questions
Evaluate that agent's JSONL records:
  .venv/bin/python hr-delivery/validation/v2/eval/nl_eval.py run --records /tmp/generated.jsonl --output-dir /tmp/hr-eval

JSONL: {"id":"q01", "question":"<原始题目>", "generated_sql":"SELECT ...",
        "context_refs":["schema.json", "rules/general.md"],
        "model":"optional", "run_metadata":{"optional":"metadata"}}
Missing/null/blank SQL is NOT_GENERATED. Unknown fields (including shell commands),
question mismatches, duplicates, malformed records are INVALID_GENERATION.
Order/empty/tolerance follow questions.py. The public output_schema fixes column
positions and ROUND display precision; aliases may differ. Raw results remain
unchanged; display rounding applies only to comparison copies. A fixed SQL replay is never labelled
as natural-language generation. Results describe submitted records only; genuine
agent provenance is the operator's responsibility.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import hashlib
import json
import math
from pathlib import Path
import sys

# Direct script execution must find both the runner and the shared root package.
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_all as regression
from hr_query.sql_policy import PolicyError, mdl_tables, validate_sql


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def output_schema(question):
    """Public result shape only: outer projection labels and explicit ROUND scale.

    No expressions, table names, filters, literals or answer values leave this
    function. Nested ROUND inside arithmetic does not define final display scale.
    """
    import sqlglot
    from sqlglot import exp

    tree = sqlglot.parse_one(question["gt"], read="duckdb")
    schema = []
    for item in tree.selects:
        if not isinstance(item, (exp.Alias, exp.Column)) or not item.alias_or_name:
            raise ValueError("标准SQL最外层投影需要显式列标题")
        expression = item.this if isinstance(item, exp.Alias) else item
        while isinstance(expression, exp.Paren):
            expression = expression.this
        digits = None
        if isinstance(expression, exp.Round):
            decimals = expression.args.get("decimals")
            if decimals is None:
                digits = 0
            elif isinstance(decimals, exp.Literal) and decimals.is_int:
                digits = int(decimals.this)
            elif (isinstance(decimals, exp.Neg) and isinstance(decimals.this, exp.Literal)
                  and decimals.this.is_int):
                digits = -int(decimals.this.this)
            else:
                raise ValueError("展示精度必须为固定整数")
        schema.append({"name": item.alias_or_name, "round_digits": digits})
    if not schema:
        raise ValueError("标准SQL缺少外层投影")
    return schema


def normalize_display(rows, schema):
    normalized = []
    for row in rows:
        values = list(row)
        for index, column in enumerate(schema):
            digits = column["round_digits"]
            if digits is None:
                continue
            try:
                number = Decimal(str(values[index]))
                if not number.is_finite():
                    continue  # comparator rejects NaN/Infinity, never hide them
                with localcontext() as context:
                    context.prec = max(28, len(number.as_tuple().digits) + abs(digits) + 2)
                    values[index] = str(number.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))
            except InvalidOperation:
                continue  # NULL/text keep their existing comparison semantics
        normalized.append(values)
    return normalized


def export_package(directory, questions, project):
    directory.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((project / "target/mdl.json").read_text(encoding="utf-8"))
    # Explicit projection: no gt/wren SQL, seed data, query examples or physical paths.
    exported = [{"id": q["id"], "question": q["question"], "domain": q["domain"],
                 "output_schema": output_schema(q)} for q in questions]
    (directory / "questions.jsonl").write_text(
        "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in exported), encoding="utf-8")
    models = []
    for model in manifest.get("models", []):
        models.append({"name": model["name"], "description": model.get("properties", {}).get("description", ""),
                       "columns": [{"name": c["name"], "type": c.get("type"),
                                    "description": c.get("properties", {}).get("description", ""),
                                    **({"expression": c["expression"]} if c.get("isCalculated") else {})}
                                   for c in model.get("columns", [])]})
    import sqlglot
    views = []
    for view in manifest.get("views", []):
        tree = sqlglot.parse_one(view["statement"], read="duckdb")
        views.append({"name": view["name"], "description": view.get("properties", {}).get("description", ""),
                      "columns": [item.alias_or_name for item in tree.selects]})
    write_json(directory / "schema.json", {"models": models, "views": views,
                                           "relationships": manifest.get("relationships", [])})
    refs = ["schema.json"]
    for category in ("rules", "glossary"):
        source = project / "knowledge" / category
        for path in sorted(source.glob("*.md")):
            destination = directory / category / path.name
            destination.parent.mkdir(exist_ok=True)
            destination.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            refs.append(f"{category}/{path.name}")
    write_json(directory / "protocol.json", {
        "version": 2, "snapshot_date": "2026-08-31", "context_refs": refs,
        "output_schema_contract": {
            "columns": "每题output_schema按结果列位置给出列标题；指标及列数必须完全一致，别名可不同。",
            "round_digits": "整数为该列公开展示精度，评测按ROUND_HALF_UP规范后比较；null表示不额外取整。",
            "raw_results": "原始生成SQL与查询结果保留，展示规范只应用于比较副本。",
        },
        "required_record_fields": ["id", "question", "generated_sql", "context_refs"],
        "optional_record_fields": ["model", "run_metadata"],
        "instructions": "仅依据题目包生成MDL SELECT，禁止读取题库/标准SQL/历史结果/knowledge/sql。"
                        "只交付JSONL记录，不能传shell命令。严格按每题output_schema输出固定列形状、指标及顺序；"
                        "遵循round_digits展示精度。列别名不限，列位置需一致。"
                        "当前指快照日2026-08-31。不得使用文件、网络或扩展函数。未能生成请写generated_sql:null。",
        "mdl_sha256": digest(project / "target/mdl.json"),
        "generation_performed": False,
    })


def load_records(path, questions):
    expected = {q["id"]: q for q in questions}
    records, errors = {}, {}
    required = {"id", "question", "generated_sql", "context_refs"}
    allowed = required | {"model", "run_metadata"}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, RecursionError) as exc:
            raise ValueError(f"JSONL第{line_number}行无效，未执行任何查询") from exc
        if (not isinstance(record, dict) or not isinstance(record.get("id"), str)
                or record["id"] not in expected):
            raise ValueError(f"JSONL第{line_number}行题号无效，未执行任何查询")
        qid = record["id"]
        if qid in records:
            errors[qid] = "题号重复"
            continue
        records[qid] = record
        refs = record.get("context_refs")
        if (set(record) - allowed or required - set(record)
                or record.get("question") != expected[qid]["question"]
                or (record.get("generated_sql") is not None and not isinstance(record["generated_sql"], str))
                or not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs)
                or ("model" in record and not isinstance(record["model"], str))
                or ("run_metadata" in record and not isinstance(record["run_metadata"], dict))):
            errors[qid] = "记录字段、原始问题或类型不符合protocol.json"
    return records, errors


def compare_answer(gt, actual, question):
    # Model-chosen aliases are presentation; compare ordered columns by position.
    gh, gr = regression.parse_csv(gt)
    ah, ar = regression.parse_csv(actual)
    schema = output_schema(question)
    if len(gh) != len(ah) or len(gh) != len(schema):
        return False, "列数不符合公开output_schema", gh
    return regression.compare(regression.table_csv(gh, normalize_display(gr, schema)),
                              regression.table_csv(gh, normalize_display(ar, schema)),
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
                                   "--datasource", "duckdb", "--mdl", mdl],
                                  cwd=project, timeout=timeout)
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
    # A new directory prevents old successes masquerading as this evaluation.
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
        for kind, text in outputs.items():
            (results / f"{qid}.{kind}.csv").write_text(text, encoding="utf-8")
        summary.append({"id": qid, "question": question["question"], "status": status, "message": message})
        print(f"{qid}: {status}")
    regression.write_summary(directory, summary, ["id", "question", "status", "message"])
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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
            command.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    try:
        questions = regression.select_questions(regression.QUESTIONS, args.only, args.domain)
        if args.command == "export":
            export_package(args.output_dir, questions, args.project.resolve())
            print(f"已导出{len(questions)}题；未生成SQL。")
            return 0
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("timeout必须为正有限秒数")
        return run_evaluation(args.output_dir, questions, args.records, args.project.resolve(),
                              args.database.resolve(), args.timeout)
    except (OSError, ValueError) as exc:
        # Never include raw generated input or environment in the error display.
        print(f"评测输入/配置错误: {type(exc).__name__}；请检查路径、JSONL协议和目录是否已存在。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
