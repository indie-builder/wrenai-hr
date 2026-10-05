"""Answer-free NL package, generation-record validation and public display precision."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import json

from reports import digest, write_json


def output_schema(question):
    """Expose only outer labels and explicit ROUND scale; never expressions/answers."""
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
            elif isinstance(decimals, exp.Neg) and isinstance(decimals.this, exp.Literal) and decimals.this.is_int:
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
                    continue  # The comparator rejects nonfinite metrics; do not round them away.
                with localcontext() as context:
                    context.prec = max(28, len(number.as_tuple().digits) + abs(digits) + 2)
                    values[index] = str(number.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))
            except InvalidOperation:
                continue
        normalized.append(values)
    return normalized


def export_package(directory, questions, project):
    import sqlglot

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
        "mdl_sha256": digest(mdl), "generation_performed": False,
    })


def reject_constant(_):
    raise ValueError("JSON常量必须为有限值")


def load_records(path, questions):
    expected = {question["id"]: question for question in questions}
    records, errors = {}, {}
    required = {"id", "question", "generated_sql", "context_refs"}
    types = {"generated_sql": (str, type(None)), "context_refs": list, "model": str, "run_metadata": dict}
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
        if (required - set(record) or set(record) - required - {"model", "run_metadata"}
                or record.get("question") != expected[qid]["question"]
                or any(not isinstance(record[key], value_type) for key, value_type in types.items() if key in record)
                or not all(isinstance(ref, str) for ref in record.get("context_refs", []))):
            errors[qid] = "记录字段、原始问题或类型不符合protocol.json"
    return records, errors
