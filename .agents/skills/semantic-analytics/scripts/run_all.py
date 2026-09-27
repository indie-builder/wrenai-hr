#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
双路径 SQL 回归 runner (领域无关泛化版)
- A 路径: gt SQL 经 duckdb 模块只读挂载物理库 (标准答案)
- B 路径: wren query 经语义层执行 (被测)
- 逐值比对 (排序 + 数值容差, 空结果判失败) → results/qXX.{gt,wren}.csv + summary.csv
用法:
  python3 run_all.py --questions <questions.py> --project <语义项目目录> \
                     --db <duckdb文件> [--results <目录>] [--tol 0.011] [--only q01 q02] [--domain <域>]
环境:
  WREN_BIN  wren CLI 路径 (默认取 PATH 上的 wren)
"""
import argparse, csv, io, os, shutil, subprocess, sys
from pathlib import Path

def load_questions(path: Path):
    sys.path.insert(0, str(path.parent))
    try:
        mod = __import__(path.stem)
    except Exception as e:
        sys.exit(f"题库加载失败 {path}: {e}")
    qs = getattr(mod, "QUESTIONS", None)
    if not qs:
        sys.exit(f"题库 {path} 未定义 QUESTIONS 列表")
    return qs

def load_env(project: Path):
    env = dict(os.environ)
    env_file = project / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    return env

def run_gt_duckdb(sql: str, db: str):
    """duckdb 只读挂载执行 gt SQL。返回 (stdout, stderr, returncode)。"""
    code = (
        "import sys, csv, duckdb\n"
        f"con = duckdb.connect({db!r}, read_only=True)\n"
        "try:\n"
        "    cur = con.execute(sys.argv[1])\n"
        "    w = csv.writer(sys.stdout)\n"
        "    w.writerow([d[0] for d in cur.description])\n"
        "    w.writerows(cur.fetchall())\n"
        "finally:\n"
        "    con.close()\n"
    )
    p = subprocess.run([sys.executable, "-c", code, sql], capture_output=True, text=True, timeout=180)
    return p.stdout, p.stderr, p.returncode

def run_wren(sql: str, wren: str, project: Path, env):
    p = subprocess.run([wren, "query", "--sql", sql, "-o", "csv", "-q"],
                       capture_output=True, text=True, timeout=180, cwd=str(project), env=env)
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

def rows_equal(r1, r2, tol):
    if len(r1) != len(r2):
        return False
    for a, b in zip(r1, r2):
        a, b = norm_cell(a), norm_cell(b)
        ka, kb = cell_key(a), cell_key(b)
        if ka[0] != kb[0]:
            return False
        if ka[0] == "n":
            if abs(ka[1] - kb[1]) > tol:
                return False
        elif ka[1] != kb[1]:
            return False
    return True

def compare(gt_text, wren_text, tol):
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
        if not rows_equal(a, b, tol):
            return False, f"第{i}行不一致 gt={a} wren={b}", gh
    return True, "OK", gh

def main():
    ap = argparse.ArgumentParser(description="双路径 SQL 回归 runner")
    ap.add_argument("--questions", required=True, help="题库 questions.py 路径")
    ap.add_argument("--project", required=True, help="语义项目目录 (wren CLI 执行目录)")
    ap.add_argument("--db", required=True, help="A 路径 duckdb 物理库文件 (只读挂载)")
    ap.add_argument("--results", help="结果输出目录 (默认: 题库同级 results/)")
    ap.add_argument("--tol", type=float, default=0.011, help="数值容差 (默认 0.011)")
    ap.add_argument("--only", nargs="*", help="只跑指定题目 id")
    ap.add_argument("--domain", help="只跑指定业务域")
    args = ap.parse_args()

    qpath = Path(args.questions).resolve()
    project = Path(args.project).resolve()
    if not Path(args.db).exists():
        sys.exit(f"物理库不存在: {args.db}")
    results = Path(args.results).resolve() if args.results else qpath.parent / "results"
    results.mkdir(parents=True, exist_ok=True)
    wren = os.environ.get("WREN_BIN", "wren")
    if shutil.which(wren) is None:
        sys.exit(f"未找到 wren CLI: {wren} (设 WREN_BIN 环境变量, 或先 pip install 'wrenai[memory]==0.13.4')")

    qs = [q for q in load_questions(qpath)
          if (not args.only or q["id"] in args.only)
          and (not args.domain or q.get("domain") == args.domain)]
    if not qs:
        sys.exit("没有匹配的题目")

    env = load_env(project)
    results_log = []
    for q in qs:
        qid = q["id"]
        gt_out, gt_err, gt_rc = run_gt_duckdb(q["gt"], str(Path(args.db).resolve()))
        wren_out, wren_err = run_wren(q["wren"], wren, project, env)
        (results / f"{qid}.gt.csv").write_text(gt_out, encoding="utf-8")
        (results / f"{qid}.wren.csv").write_text(wren_out, encoding="utf-8")
        if gt_rc != 0:
            detail = gt_err.strip().splitlines()[-1] if gt_err.strip() else "无 stderr (A 路径需 duckdb 模块: pip install duckdb)"
            ok, msg = False, f"GT执行失败: {detail[:160]}"
        elif "Error" in wren_err or "error" in wren_err:
            ok, msg = False, f"Wren执行失败: {wren_err.strip().splitlines()[-1][:160]}"
        else:
            ok, msg, _ = compare(gt_out, wren_out, args.tol)
        results_log.append({**q, "result": "PASS" if ok else "FAIL", "msg": msg})
        print(f"{'✅' if ok else '❌'} {qid:>5} [{q.get('domain','')}|{q.get('priority','')}] {msg}")

    with open(results / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "domain", "priority", "question", "result", "msg"])
        for r in results_log:
            w.writerow([r["id"], r.get("domain", ""), r.get("priority", ""), r["question"], r["result"], r["msg"]])

    total = len(results_log)
    passed = sum(1 for r in results_log if r["result"] == "PASS")
    p0 = [r for r in results_log if r.get("priority") == "P0"]
    p0_pass = sum(1 for r in p0 if r["result"] == "PASS")
    print(f"\n===== 汇总: {passed}/{total} PASS | P0口径题 {p0_pass}/{len(p0)} PASS =====")
    fails = [r for r in results_log if r["result"] == "FAIL"]
    if fails:
        print("失败题目:", ", ".join(r["id"] for r in fails))
    sys.exit(0 if passed == total else 1)

if __name__ == "__main__":
    main()
