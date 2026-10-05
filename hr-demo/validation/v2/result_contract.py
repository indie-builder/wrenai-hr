"""CSV/table result contract shared by SQL regression, NL evaluation and exports.

Only standard-library dependencies: this module can travel with an offline runner.
Unordered results are bags; numeric matching never discards duplicate rows.
"""
import csv
from decimal import Decimal, InvalidOperation
import io

NUM_TOL = 0.011


def table_csv(columns, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(columns)
    writer.writerows([["" if value is None else value for value in row] for row in rows])
    return stream.getvalue()


def parse_csv(text):
    if not text or not text.endswith(("\n", "\r")):
        raise ValueError("CSV输出缺失或不完整")
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise ValueError("CSV格式错误") from exc
    # Wren 0.13.4 adds one trailing blank line to pandas' CSV.
    while rows and rows[-1] == []:
        rows.pop()
    headers, values = (rows[0], rows[1:]) if rows else ([], [])
    return validate_table([header.strip() for header in headers], values)


def validate_table(headers, rows):
    if not headers or any(not isinstance(header, str) or not header.strip() for header in headers):
        raise ValueError("CSV表头缺失")
    if len(headers) != len(set(headers)):
        raise ValueError("CSV列名重复")
    if any(len(row) != len(headers) for row in rows):
        raise ValueError("CSV行列数不完整")
    return headers, rows


def norm_cell(cell):
    value = "" if cell is None else str(cell).strip()
    if value in ("", "NULL", "None"):
        return ""
    if value.lower() in ("t", "true"):
        return "true"
    if value.lower() in ("f", "false"):
        return "false"
    return value


def cell_key(cell):
    value = norm_cell(cell)
    try:
        return "n", Decimal(value)
    except InvalidOperation:
        return "s", value


def numeric_tolerance(value):
    try:
        tolerance = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("tolerance必须为非负有限数值") from exc
    if not tolerance.is_finite() or tolerance < 0:
        raise ValueError("tolerance必须为非负有限数值")
    return tolerance


def rows_equal(left, right, tolerance=NUM_TOL):
    tol = numeric_tolerance(tolerance)
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        ka, kb = cell_key(a), cell_key(b)
        if ka[0] != kb[0]:
            return False
        if ka[0] == "n":
            if not ka[1].is_finite() or not kb[1].is_finite() or abs(ka[1] - kb[1]) > tol:
                return False
        elif ka[1] != kb[1]:
            return False
    return True


def unmatched_row(left, right, tolerance):
    # Augmenting paths avoid greedy failures near a tolerance boundary.
    candidates = [[j for j, row in enumerate(right) if rows_equal(value, row, tolerance)] for value in left]
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
                   ordered=False, allow_empty=False, tolerance=NUM_TOL):
    tol = numeric_tolerance(tolerance)
    if not isinstance(ordered, bool) or not isinstance(allow_empty, bool):
        raise ValueError("ordered和allow_empty必须为bool")
    try:
        gh, gr = validate_table(left_headers, left_rows)
        wh, wr = validate_table(right_headers, right_rows)
    except ValueError as exc:
        return False, str(exc), []
    if gh != wh:
        return False, f"列名不一致 gt={gh} wren={wh}", gh
    if len(gr) != len(wr):
        return False, f"行数不一致 gt={len(gr)} wren={len(wr)}", gh
    if not gr:
        return (True, "OK (允许空结果)", gh) if allow_empty else (False, "空结果", gh)
    if ordered:
        for index, (left, right) in enumerate(zip(gr, wr), 1):
            if not rows_equal(left, right, tol):
                return False, f"第{index}行不一致（保留原始顺序）", gh
    else:
        index = unmatched_row(gr, wr, tol)
        if index is not None:
            return False, f"第{index + 1}行无等价匹配（含重复行计数）", gh
    return True, "OK", gh


def compare(gt_text, wren_text, **options):
    # Validate options even if a process returned malformed CSV.
    numeric_tolerance(options.get("tolerance", NUM_TOL))
    if any(not isinstance(options.get(key, False), bool) for key in ("ordered", "allow_empty")):
        raise ValueError("ordered和allow_empty必须为bool")
    try:
        gh, gr = parse_csv(gt_text)
        wh, wr = parse_csv(wren_text)
    except ValueError as exc:
        return False, str(exc), []
    return compare_tables(gh, gr, wh, wr, **options)


def comparison_options(question):
    return {key: question.get(key, default) for key, default in
            (("ordered", False), ("allow_empty", False), ("tolerance", NUM_TOL))}
