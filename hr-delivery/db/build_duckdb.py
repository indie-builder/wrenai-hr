#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建 DuckDB 演示库 (数据层唯一版本):
  - schema: schema_duckdb.sql (主键由本脚本装载时生成)
  - 数据:   seed/out 与 seed/out2 的 CSV (gen_hr_data*.py 生成, 随仓库分发)
  - 考勤:   默认读取 seed/attendance_records.parquet 确定性种子。
             缺少种子直接失败，不隐式改用随机数据。
      generate 显式指定时用 gen_attendance_duckdb.sql 重新生成，
               是另一批模拟数据，不能沿用已保存的回归结果。

文件必须命名为 public.duckdb: wren 的 duckdb 连接器按文件名为挂载 catalog 别名,
MDL 规划 SQL 以 "public" 前缀限定物理表 ("public".employees)。

用法: ../../.venv/bin/python build_duckdb.py [--attendance parquet|generate]
"""
import argparse
import json
import hashlib
import sys
import tempfile
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
OUT = HERE / "seed" / "out"
OUT2 = HERE / "seed" / "out2"
DB_DIR = HERE / "duckdb"
DB_FILE = DB_DIR / "public.duckdb"
ATT_PARQUET = HERE / "seed" / "attendance_records.parquet"
ATT_MANIFEST = HERE / "seed" / "attendance_manifest.json"

# CSV 含显式主键、整行装载的表
FULL_TABLES = ["departments", "employees"]

# CSV 不含主键的表: 表名 -> (CSV 目录, 主键列, 业务列清单, 与 CSV 列序一致)
# 装载时按文件顺序生成主键
COL_TABLES = {
    "salary_payments": (OUT, "pay_id",
        "emp_id,pay_period,base_pay,overtime_pay,bonus,social_insurance,income_tax,net_pay,pay_date"),
    "leave_requests": (OUT, "leave_id",
        "emp_id,leave_type,start_date,end_date,days,status,approver_id,applied_at"),
    "job_openings": (OUT, "opening_id",
        "dept_id,job_title,job_level,headcount,salary_min,salary_max,status,opened_at,closed_at,hired_count"),
    "candidates": (OUT, "cand_id",
        "name,gender,opening_id,source,stage,applied_at,expected_salary,hired_emp_id"),
    "interviews": (OUT, "interview_id",
        "cand_id,round,interviewer_id,interview_date,score,result"),
    "performance_reviews": (OUT, "review_id",
        "emp_id,review_period,score,grade,reviewer_id,comment"),
    "training_records": (OUT, "training_id",
        "emp_id,course_name,training_date,hours,completed,score"),
    "transfers": (OUT, "transfer_id",
        "emp_id,from_dept_id,to_dept_id,transfer_date,reason"),
    "headcount_plan": (OUT2, "plan_id",
        "plan_year,dept_id,planned_headcount,budget_labor_cost,approved_at"),
    "promotions": (OUT2, "promo_id",
        "emp_id,promo_date,from_level,to_level,from_title,to_title,salary_before,salary_after,reason"),
    "contracts": (OUT2, "contract_id",
        "contract_no,emp_id,contract_type,start_date,end_date,renewals,status,signed_date"),
    "salary_changes": (OUT2, "change_id",
        "emp_id,effective_date,salary_before,salary_after,change_pct,change_type"),
    "insurance_payments": (OUT2, "ins_id",
        "emp_id,pay_period,pension,medical,unemployment,injury,maternity,housing_fund,company_total"),
    "awards_penalties": (OUT2, "record_id",
        "emp_id,record_date,record_type,category,amount,reason,approver_id"),
    "overtime_requests": (OUT2, "ot_id",
        "emp_id,ot_date,planned_hours,actual_hours,reason,status,compensation,applied_at"),
    "leave_balances": (OUT2, "balance_id",
        "emp_id,balance_type,as_of_quarter,entitled,used,remaining,expired"),
    "offers": (OUT2, "offer_id",
        "cand_id,offer_date,offer_salary,status,response_date,reject_reason"),
    "recruitment_costs": (OUT2, "cost_id",
        "channel,cost_month,amount,notes"),
    "performance_goals": (OUT2, "goal_id",
        "emp_id,review_period,goal_type,goal_desc,weight,completion_pct"),
    "talent_pool": (OUT2, "pool_id",
        "emp_id,pool_type,target_position,potential_rating,nominated_date,nominated_by,status"),
    "engagement_surveys": (OUT2, "survey_id",
        "emp_id,survey_year,engagement_score,recognition,growth,pay_satisfaction,manager_trust,work_life_balance"),
    "exit_interviews": (OUT2, "exit_id",
        "emp_id,interview_date,real_reason_category,satisfaction,would_recommend,comment"),
}

ALL_TABLES = sorted(FULL_TABLES + ["attendance_records"] + list(COL_TABLES))


def read_header(csv_path: Path) -> list[str]:
    with open(csv_path, encoding="utf-8-sig") as f:
        return [h.strip() for h in f.readline().strip().split(",")]


def split_statements(sql_text: str) -> list[str]:
    # schema 中注释串不含分号, 按分号切分即可
    return [s.strip() for s in sql_text.split(";") if s.strip()]


def resolve_attendance(requested: str) -> str:
    if requested == "generate":
        return "generate"
    if not ATT_PARQUET.exists() or not ATT_MANIFEST.exists():
        raise SystemExit("错误: 缺少 seed/attendance_records.parquet 或其来源清单；未修改现有数据库。")
    expected = json.loads(ATT_MANIFEST.read_text(encoding="utf-8"))["sha256"]
    if hashlib.sha256(ATT_PARQUET.read_bytes()).hexdigest() != expected:
        raise SystemExit("错误: 考勤种子 SHA-256 与来源清单不一致；未修改现有数据库。")
    return "parquet"


def load_attendance_parquet(con) -> None:
    if not ATT_PARQUET.exists():
        raise SystemExit(f"错误: 未找到 {ATT_PARQUET}")
    cols = "emp_id,att_date,status,work_hours,overtime_hours"
    # parquet 含 PG 原始 att_id, 直接沿用 (并行扫描下 row_number() 不保序, 不用)
    con.execute(
        f"INSERT INTO attendance_records (att_id, {cols}) "
        f"SELECT att_id, {cols} "
        "FROM read_parquet(?) ORDER BY att_id", [str(ATT_PARQUET)])


def build(attendance: str) -> int:
    attendance = resolve_attendance(attendance)
    if attendance == "generate":
        print("警告: 显式重生成考勤会改变模拟数据，需重新验证并导出仪表盘快照。")
    for table in FULL_TABLES:
        if not (OUT / f"{table}.csv").is_file():
            raise SystemExit(f"错误: 缺少种子 {table}.csv；未修改现有数据库。")
    for table, (source, _, _) in COL_TABLES.items():
        if not (source / f"{table}.csv").is_file():
            raise SystemExit(f"错误: 缺少种子 {table}.csv；未修改现有数据库。")

    DB_DIR.mkdir(exist_ok=True)
    if DB_FILE.with_suffix(".duckdb.wal").exists():
        raise SystemExit("错误: 存在数据库 WAL，请先正常关闭所有连接；未修改现有数据库。")
    staging = tempfile.TemporaryDirectory(prefix=".build-", dir=DB_DIR)
    staged_file = Path(staging.name) / "public.duckdb"
    con = duckdb.connect(str(staged_file))
    completed = False
    try:
        # CSV 无主键时沿文件顺序编号，固定单线程并保留输入顺序。
        con.execute("SET threads = 1")
        con.execute("SET preserve_insertion_order = true")
        for stmt in split_statements((HERE / "schema_duckdb.sql").read_text(encoding="utf-8")):
            con.execute(stmt)
        print("== 1/3 schema_duckdb.sql OK (25 tables)")

        for t in FULL_TABLES:
            csv_path = OUT / f"{t}.csv"
            expected = [r[1] for r in con.execute(
                "SELECT ordinal_position, column_name FROM information_schema.columns "
                "WHERE table_name = ? ORDER BY ordinal_position", [t]).fetchall()]
            if read_header(csv_path) != expected:
                print(f"错误: {csv_path.name} 列头与 {t} 表结构不一致")
                return 1
            con.execute(
                f"INSERT INTO {t} SELECT * FROM read_csv(?, header=true, sample_size=-1)",
                [str(csv_path)])
            print(f"  {t} OK")

        for t, (src_dir, pk, cols) in COL_TABLES.items():
            csv_path = src_dir / f"{t}.csv"
            if read_header(csv_path) != cols.split(","):
                print(f"错误: {csv_path.name} 列头与装载列清单不一致")
                return 1
            con.execute(
                f"INSERT INTO {t} ({pk}, {cols}) "
                f"SELECT row_number() OVER (), {cols} "
                "FROM read_csv(?, header=true, sample_size=-1)", [str(csv_path)])
            print(f"  {t} OK")

        if attendance == "parquet":
            load_attendance_parquet(con)
            print(f"  attendance_records OK (parquet 快照: {ATT_PARQUET.name})")
        else:
            print("== 考勤: 用 gen_attendance_duckdb.sql 显式重新生成")
            con.execute((HERE / "gen_attendance_duckdb.sql").read_text(encoding="utf-8"))

        print("== 行数统计 ==")
        total = 0
        for t in ALL_TABLES:
            (n,) = con.execute(f"SELECT count(*) FROM {t}").fetchone()
            total += n
            print(f"  {t:22s} {n:>8}")
        print(f"  {'合计':22s} {total:>8}")
        completed = True
    finally:
        try:
            con.close()
            if completed:
                staged_file.replace(DB_FILE)
        finally:
            staging.cleanup()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="构建 hr-demo DuckDB 库 public.duckdb")
    ap.add_argument("--attendance", choices=["auto", "parquet", "generate"],
                    default="auto",
                    help="考勤来源 (auto/parquet 均要求确定性种子；generate 显式重生成)")
    sys.exit(build(ap.parse_args().attendance))
