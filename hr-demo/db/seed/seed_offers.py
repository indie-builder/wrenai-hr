"""Offers and recruiting expenses linked to the baseline candidate pipeline."""
import datetime as dt

from seed_common import MONTHS, REF


def offers(source):
    rng, rows = source.rng, []
    for candidate in source.candidates:
        if candidate["stage"] not in ("已入职", "已发offer", "拒绝offer") or not candidate["expected_salary"]:
            continue
        day = candidate["applied_at"] + dt.timedelta(days=rng.randint(12, 25))
        if day >= REF:
            continue
        if candidate["stage"] == "已入职":
            status, response, reason = "已接受", day + dt.timedelta(days=rng.randint(1, 5)), None
        elif candidate["stage"] == "已发offer":
            status, response, reason = "待回复", None, None
        else:
            status, response = "已拒绝", day + dt.timedelta(days=rng.randint(2, 8))
            reason = rng.weighted([("薪酬不匹配", 40), ("已接其他offer", 30), ("家庭原因", 18), ("其他", 12)])
        rows.append({"cand_id": candidate["cand_id"], "offer_date": day, "offer_salary": candidate["expected_salary"],
                     "status": status, "response_date": response, "reject_reason": reason})
    return rows


def costs(source):
    rng, rows, hires = source.rng, [], {}
    for candidate in source.candidates:
        if candidate["stage"] == "已入职" and candidate["hired_emp_id"]:
            day = source.by_id[candidate["hired_emp_id"]]["hire_date"]
            key = day.year, day.month, candidate["source"]
            hires[key] = hires.get(key, 0) + 1
    for year, month in MONTHS:
        period = f"{year:04d}-{month:02d}"
        rows.append({"channel": "招聘网站", "cost_month": period, "amount": float(round(rng.uniform(8000, 15000), 2)),
                     "notes": "年度框架会员费分摊"})
        if rng.random() < 0.25:
            rows.append({"channel": "猎头", "cost_month": period, "amount": float(round(rng.uniform(15000, 40000), 2)),
                         "notes": "按到岗候选人服务费"})
        seasonal = 2.5 if month in (3, 4, 9, 10) else 1.0
        rows.append({"channel": "校园招聘", "cost_month": period,
                     "amount": float(round(rng.uniform(2000, 8000) * seasonal, 2)), "notes": "宣讲会与双选会费用"})
        referrals = hires.get((year, month, "内推"), 0)
        if referrals:
            rows.append({"channel": "内推奖励", "cost_month": period, "amount": float(referrals * 2000),
                         "notes": f"内推入职{referrals}人×2000元"})
    return rows
