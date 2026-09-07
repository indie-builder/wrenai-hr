#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端验证一体化 runner (GOAL.md M3)
- A 路径: psql --csv 直连物理表 (标准答案)
- B 路径: wren query -o csv 经 MDL 语义层执行
- 逐值比对 → results/qXX.{gt,wren}.csv + summary.csv + matrix_v2_auto.md
用法:
  python3 run_all.py                 # 全量
  python3 run_all.py --only q13 q21  # 指定题目
  python3 run_all.py --domain 人效分析
"""
import argparse, csv, io, os, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from questions import QUESTIONS

ROOT = HERE.parent.parent              # wren-project 的上级 (hr-delivery)
PROJECT = ROOT / "wren-project"
WREN = ROOT.parent / ".venv" / "bin" / "wren"
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

# 加载 wren-project/.env (凭据不进代码)
env = dict(os.environ)
for line in (PROJECT / ".env").read_text().splitlines():
    if line.strip() and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        env[k] = v

NUM_TOL = 0.011  # 数值容差

def run_gt(sql):
    p = subprocess.run(["docker", "exec", "-i", "wrenai-hr-pg", "psql", "-U", "hr",
                        "-d", "hr_demo", "--csv", "-v", "ON_ERROR_STOP=1", "-c", sql],
                       capture_output=True, text=True, timeout=180)
    return p.stdout, p.stderr

def run_wren(sql):
    p = subprocess.run([str(WREN), "query", "--sql", sql, "-o", "csv", "-q"],
                       capture_output=True, text=True, timeout=180, cwd=str(PROJECT), env=env)
    return p.stdout, p.stderr

def parse_csv(text):
    rows = list(csv.reader(io.StringIO(text.strip())))
    if not rows:
        return [], []
    return [h.strip() for h in rows[0]], rows[1:]

def norm_cell(c):
    if c is None:
        return ""
    c = c.strip()
    if c in ("", "NULL", "None"):
        return ""
    return c

def cell_key(c):
    try:
        return ("n", round(float(c), 4))
    except ValueError:
        return ("s", c)

def rows_equal(r1, r2):
    if len(r1) != len(r2):
        return False
    for a, b in zip(r1, r2):
        a, b = norm_cell(a), norm_cell(b)
        ka, kb = cell_key(a), cell_key(b)
        if ka[0] != kb[0]:
            return False
        if ka[0] == "n":
            if abs(ka[1] - kb[1]) > NUM_TOL:
                return False
        elif ka[1] != kb[1]:
            return False
    return True

def compare(gt_text, wren_text):
    gh, gr = parse_csv(gt_text)
    wh, wr = parse_csv(wren_text)
    if not gr or not wr:
        return False, "空结果", gh or wh
    g_sorted = sorted([[norm_cell(c) for c in r] for r in gr])
    w_sorted = sorted([[norm_cell(c) for c in r] for r in wr])
    if len(g_sorted) != len(w_sorted):
        return False, f"行数不一致 gt={len(g_sorted)} wren={len(w_sorted)}", gh
    if [h.strip() for h in gh] != [h.strip() for h in wh]:
        return False, f"列名不一致 gt={gh} wren={wh}", gh
    for i, (a, b) in enumerate(zip(g_sorted, w_sorted)):
        if not rows_equal(a, b):
            return False, f"第{i}行不一致 gt={a} wren={b}", gh
    return True, "OK", gh

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="只跑指定题目 id")
    ap.add_argument("--domain", help="只跑指定业务域")
    args = ap.parse_args()

    qs = [q for q in QUESTIONS
          if (not args.only or q["id"] in args.only)
          and (not args.domain or q["domain"] == args.domain)]

    results = []
    for q in qs:
        qid = q["id"]
        gt_out, gt_err = run_gt(q["gt"])
        wren_out, wren_err = run_wren(q["wren"])
        (RESULTS / f"{qid}.gt.csv").write_text(gt_out, encoding="utf-8")
        (RESULTS / f"{qid}.wren.csv").write_text(wren_out, encoding="utf-8")
        if "ERROR" in gt_err:
            ok, msg, _ = False, f"GT执行失败: {gt_err.strip().splitlines()[-1][:160]}", []
        elif "Error" in wren_err or "error" in wren_err:
            ok, msg, _ = False, f"Wren执行失败: {wren_err.strip().splitlines()[-1][:160]}", []
        else:
            ok, msg, _ = compare(gt_out, wren_out)
        results.append({**q, "result": "PASS" if ok else "FAIL", "msg": msg})
        flag = "✅" if ok else "❌"
        print(f"{flag} {qid:>5} [{q['domain']}|{q['priority']}] {msg}")

    # summary.csv
    with open(HERE / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "domain", "priority", "question", "result", "msg"])
        for r in results:
            w.writerow([r["id"], r["domain"], r["priority"], r["question"], r["result"], r["msg"]])

    # 汇总
    total = len(results)
    passed = sum(1 for r in results if r["result"] == "PASS")
    p0 = [r for r in results if r["priority"] == "P0"]
    p0_pass = sum(1 for r in p0 if r["result"] == "PASS")
    print(f"\n===== 汇总: {passed}/{total} PASS | P0口径题 {p0_pass}/{len(p0)} PASS =====")
    fails = [r for r in results if r["result"] == "FAIL"]
    if fails:
        print("失败题目:", ", ".join(r["id"] for r in fails))
    sys.exit(0 if passed == total else 1)

if __name__ == "__main__":
    main()
