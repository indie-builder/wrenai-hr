"""Answer-free NL package, public result schema and generation-record validation."""
import json
import shutil

import sqlglot
from sqlglot import exp

from hr_analytics.execution import digest, write_json

RECORD_TYPES = {"id": str, "question": str, "generated_sql": (str, type(None)), "context_refs": list}
OPTIONAL_TYPES = {"model": str, "run_metadata": dict}


def output_schema(question):
    """Expose only outer labels and explicit ROUND scale, never expressions/answers."""
    schema = []
    for item in sqlglot.parse_one(question["gt"], read="duckdb").selects:
        if not isinstance(item, (exp.Alias, exp.Column)) or not item.alias_or_name:
            raise ValueError("标准SQL最外层投影需要显式列标题")
        expression = (item.this if isinstance(item, exp.Alias) else item).unnest()
        digits = None
        if isinstance(expression, exp.Round):
            decimals = expression.args.get("decimals") or exp.Literal.number(0)
            if not decimals.is_int:
                raise ValueError("展示精度必须为固定整数")
            digits = int(decimals.sql())
        schema.append({"name": item.alias_or_name, "round_digits": digits})
    if not schema:
        raise ValueError("标准SQL缺少外层投影")
    return schema


def export_package(directory, questions, project):
    directory.mkdir(parents=True, exist_ok=False)
    mdl = project / "target/mdl.json"
    manifest = json.loads(mdl.read_text(encoding="utf-8"))
    public_questions = [{"id": q["id"], "question": q["question"], "domain": q["domain"],
                         "output_schema": output_schema(q)} for q in questions]
    (directory / "questions.jsonl").write_text(
        "".join(json.dumps(question, ensure_ascii=False) + "\n" for question in public_questions), encoding="utf-8")
    models = [{"name": model["name"], "description": model.get("properties", {}).get("description", ""),
               "columns": [{"name": column["name"], "type": column.get("type"),
                            "description": column.get("properties", {}).get("description", ""),
                            **({"expression": column["expression"]} if column.get("isCalculated") else {})}
                           for column in model.get("columns", [])]} for model in manifest.get("models", [])]
    views = [{"name": view["name"], "description": view.get("properties", {}).get("description", ""),
              "columns": [item.alias_or_name for item in sqlglot.parse_one(view["statement"], read="duckdb").selects]}
             for view in manifest.get("views", [])]
    write_json(directory / "schema.json", {"models": models, "views": views,
                                           "relationships": manifest.get("relationships", [])})
    refs = ["schema.json"]
    for category in ("rules", "glossary"):
        for path in sorted((project / "knowledge" / category).glob("*.md")):
            destination = directory / category / path.name
            destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(path, destination)
            refs.append(f"{category}/{path.name}")
    write_json(directory / "protocol.json", {
        "version": 2, "snapshot_date": "2026-08-31", "context_refs": refs,
        "output_schema_contract": {
            "columns": "每题output_schema按结果列位置给出列标题；指标及列数必须完全一致，别名可不同。",
            "round_digits": "整数为该列公开展示精度，评测按ROUND_HALF_UP规范后比较；null表示不额外取整。",
            "raw_results": "原始生成SQL与查询结果保留，展示规范只应用于比较副本。",
        },
        "required_record_fields": list(RECORD_TYPES), "optional_record_fields": list(OPTIONAL_TYPES),
        "instructions": "仅依据题目包生成MDL SELECT，禁止读取题库/标准SQL/历史结果/knowledge/sql。"
                        "只交付JSONL记录，不能传shell命令。严格按每题output_schema输出固定列形状、指标及顺序；"
                        "遵循round_digits展示精度。列别名不限，列位置需一致。"
                        "当前指快照日2026-08-31。不得使用文件、网络或扩展函数。未能生成请写generated_sql:null。",
        "mdl_sha256": digest(mdl), "generation_performed": False,
    })


def reject_constant(_):
    raise ValueError("JSON常量必须为有限值")


def load_records(path, questions):
    expected = {question["id"]: question["question"] for question in questions}
    records, errors = {}, {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line, parse_constant=reject_constant)
        except (ValueError, RecursionError) as exc:
            raise ValueError(f"JSONL第{number}行无效，未执行任何查询") from exc
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or record["id"] not in expected:
            raise ValueError(f"JSONL第{number}行题号无效，未执行任何查询")
        qid = record["id"]
        if qid in records:
            errors[qid] = "题号重复"
            continue
        records[qid] = record
        if (RECORD_TYPES.keys() - record.keys() or record.keys() - RECORD_TYPES.keys() - OPTIONAL_TYPES.keys()
                or record.get("question") != expected[qid]
                or any(not isinstance(record[key], kind) for key, kind in (RECORD_TYPES | OPTIONAL_TYPES).items() if key in record)
                or not all(isinstance(ref, str) for ref in record.get("context_refs", []))):
            errors[qid] = "记录字段、原始问题或类型不符合protocol.json"
    return records, errors
