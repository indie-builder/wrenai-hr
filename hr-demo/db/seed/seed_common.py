#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""种子生成器公共助手: 月份/工作日随机、加权抽取、字典行 → CSV 写出"""
import csv, os, random, datetime as dt


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
