#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""种子生成器公共助手: 月份/工作日随机、加权抽取、字典行 → CSV 写出"""
import csv
import datetime as dt
import os
from pathlib import Path
import random
import re

REF = dt.date(2026, 9, 1)  # Exclusive end of the fixed 2026-08-31 snapshot.
MONTHS = [(2023 + i // 12, i % 12 + 1) for i in range(44)]
LEVELS = ["初级", "中级", "高级", "专家", "总监", "副总裁"]


def month_days(y, m):
    nxt = dt.date(y + (m == 12), (m % 12) + 1, 1)
    return (nxt - dt.date(y, m, 1)).days


def rand_workday(d0, d1, rng=random):
    """[d0, d1] 内随机取一个工作日 (最多尝试 30 次, 退化返回 d0)"""
    if d0 > d1:
        d0 = d1
    span = (d1 - d0).days
    for _ in range(30):
        c = d0 + dt.timedelta(days=rng.randint(0, max(span, 0)))
        if c.weekday() < 5:
            return c
    return d0


def month_rand_workday(y, m, rng=random):
    return rand_workday(dt.date(y, m, 1), dt.date(y, m, month_days(y, m)), rng)


def weighted(pairs, rng=random):
    xs, ws = zip(*pairs)
    return rng.choices(xs, weights=ws, k=1)[0]


class SeedRandom(random.Random):
    """One explicit random stream for every ordered stage of a simulation."""

    def weighted(self, pairs):
        return weighted(pairs, self)

    def workday(self, start, end):
        return rand_workday(start, end, self)

    def month_workday(self, year, month):
        return month_rand_workday(year, month, self)


def active_at(employee, day):
    return employee["hire_date"] <= day and (employee["termination_date"] is None
                                            or employee["termination_date"] >= day)


def base_at(employee, day):
    """Historical monthly base salary: the same annual 3% rule for both seeds."""
    years = max(0.0, (REF - day).days / 365.25)
    return max(4000, round(employee["base_salary"] * 1.03 ** (-years) / 100) * 100)


def read_csv(directory, name, **converters):
    """Parse only fields used in simulation; optional numeric/date blanks stay None."""
    with (Path(directory) / name).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        for field, convert in converters.items():
            row[field] = convert(row[field]) if row[field] else None
    return rows


def write_tables(directory, **tables):
    """Export in canonical schema order, omitting generated primary keys.

    Contracts retain their historical CSV field order. Reading column names from
    the CREATE source keeps generator and loader contracts in one place.
    """
    schema = (Path(__file__).resolve().parents[1] / "schema_duckdb.sql").read_text(encoding="utf-8")
    columns = {}
    for table, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\n\);", schema, re.S):
        fields = []
        for line in body.splitlines():
            match = re.match(r"\s+(\w+)\s+(?:INTEGER|VARCHAR|CHAR|DATE|TIMESTAMP|NUMERIC|BOOLEAN|TEXT)\b", line)
            if match and ("PRIMARY KEY" not in line or table in ("departments", "employees")):
                fields.append(match[1])
        columns[table] = fields
    columns["contracts"] = ["contract_no"] + [field for field in columns["contracts"] if field != "contract_no"]
    for table, rows in tables.items():
        write_csv(directory, f"{table}.csv", columns[table], rows)
        print(f"{table:<18} {len(rows)}")


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "t" if v else "f"
    return v


def write_csv(out_dir, name, header, rows):
    """按 header 顺序写出字典行; None→空串, True/False→t/f; LF 行尾与仓库快照一致"""
    with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow([_cell(r[k]) for k in header])
