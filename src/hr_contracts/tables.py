"""Complete CSV/table comparison, including public display precision.

Only standard-library dependencies: this module can travel with an offline runner.
Unordered results are bags; numeric matching never discards duplicate rows.
"""
import csv
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import io

NUM_TOL = 0.011


def table_csv(columns, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(columns)
    writer.writerows(rows)
    return stream.getvalue()


def parse_csv(text):
    if not text or not text.endswith(("\n", "\r")):
        raise ValueError("CSV输出缺失或不完整")
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise ValueError("CSV格式错误") from exc
    # Wren appends a blank line to pandas' CSV.
    while rows and rows[-1] == []:
        rows.pop()
    headers, values = (rows[0], rows[1:]) if rows else ([], [])
    return validate_table([header.strip() for header in headers], values)


def validate_table(headers, rows):
    if not headers or any(not isinstance(header, str) or not header.strip() for header in headers):
        raise ValueError("CSV表头缺失")
    if len(headers) != len({header.strip() for header in headers}):
        raise ValueError("CSV列名重复")
    if any(len(row) != len(headers) for row in rows):
        raise ValueError("CSV行列数不完整")
    return headers, rows


def cell_value(cell, digits=None):
    value = "" if cell is None else str(cell).strip()
    try:
        number = Decimal(value)
    except InvalidOperation:
        if value in ("", "NULL", "None"):
            return ""
        return {"t": "true", "true": "true", "f": "false", "false": "false"}.get(value.lower(), value)
    if digits is not None and number.is_finite():
        try:
            with localcontext() as context:
                context.prec = max(28, len(number.as_tuple().digits) + abs(digits) + 2, number.adjusted() + digits + 2)
                number = number.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            pass  # Values outside Decimal's exponent range retain their exact finite value.
    return number


def numeric_tolerance(value):
    try:
        tolerance = Decimal(str(value))
        if tolerance.is_finite() and tolerance >= 0:
            return tolerance
    except InvalidOperation:
        pass
    raise ValueError("tolerance必须为非负有限数值")


def rows_match(left, right, tolerance):
    for a, b in zip(left, right):
        if isinstance(a, Decimal) and isinstance(b, Decimal):
            if not a.is_finite() or not b.is_finite() or abs(a - b) > tolerance:
                return False
        elif a != b:
            return False
    return True


def unmatched_row(left, right, tolerance):
    # Augmenting paths avoid greedy failures near a tolerance boundary.
    candidates = [[j for j, row in enumerate(right) if rows_match(value, row, tolerance)] for value in left]
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

    return next((i for i in sorted(range(len(left)), key=lambda i: len(candidates[i]))
                 if not match(i, set())), None)


def compare_tables(left_headers, left_rows, right_headers, right_rows, *,
                   ordered=False, allow_empty=False, tolerance=NUM_TOL, output_schema=None):
    tol = numeric_tolerance(tolerance)
    if not isinstance(ordered, bool) or not isinstance(allow_empty, bool):
        raise ValueError("ordered和allow_empty必须为bool")
    try:
        gh, gr = validate_table(left_headers, left_rows)
        wh, wr = validate_table(right_headers, right_rows)
    except ValueError as exc:
        return False, str(exc), []
    if output_schema is not None:
        if len(gh) != len(wh) or len(gh) != len(output_schema):
            return False, "列数不符合公开output_schema", gh
    elif gh != wh:
        return False, f"列名不一致 gt={gh} wren={wh}", gh
    if len(gr) != len(wr):
        return False, f"行数不一致 gt={len(gr)} wren={len(wr)}", gh
    if not gr:
        return (True, "OK (允许空结果)", gh) if allow_empty else (False, "空结果", gh)
    digits = [column["round_digits"] for column in output_schema] if output_schema is not None else [None] * len(gh)
    gr, wr = [[[cell_value(cell, scale) for cell, scale in zip(row, digits)] for row in rows] for rows in (gr, wr)]
    if ordered:
        for index, (left, right) in enumerate(zip(gr, wr), 1):
            if not rows_match(left, right, tol):
                return False, f"第{index}行不一致（保留原始顺序）", gh
    else:
        index = unmatched_row(gr, wr, tol)
        if index is not None:
            return False, f"第{index + 1}行无等价匹配（含重复行计数）", gh
    return True, "OK", gh


def compare(gt_text, wren_text, *, output_schema=None, **options):
    options = comparison_options(options)
    try:
        gh, gr = parse_csv(gt_text)
        wh, wr = parse_csv(wren_text)
    except ValueError as exc:
        return False, str(exc), []
    return compare_tables(gh, gr, wh, wr, output_schema=output_schema, **options)


def comparison_options(question):
    options = {key: question.get(key, default) for key, default in
               (("ordered", False), ("allow_empty", False), ("tolerance", NUM_TOL))}
    numeric_tolerance(options["tolerance"])
    if any(not isinstance(options[key], bool) for key in ("ordered", "allow_empty")):
        raise ValueError("ordered和allow_empty必须为bool")
    return options
