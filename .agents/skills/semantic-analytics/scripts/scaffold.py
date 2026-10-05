#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新业务域脚手架生成器
- 生成 semantic-analytics 工作流 ① 阶段的目录骨架与占位文件
  (semantic/ 已含 wren_project.yml, data_source=duckdb, 无需手工 wren init)
- 占位文件内含 TODO 指引, 按 SKILL.md 的 ②~⑥ 阶段逐个填充
用法:
  python3 scaffold.py --domain finance --root <项目根>
"""
import argparse, re, sys
from pathlib import Path

QUESTIONS_TEMPLATE = '''# -*- coding: utf-8 -*-
"""
{domain} 域双路径题库
每题五要素: id / domain / priority / question / gt / wren
- gt:   直连物理表的标准答案 SQL
- wren: 经语义层执行的被测 SQL (模型名 = MDL 模型)
可选比对选项 (括号内为默认值):
- ordered=False   排名/趋势等有序结果置 True, 逐行保序比对
- allow_empty=False  空结果合法的题置 True, 否则空结果判 FAIL
- tolerance=0.011 数值容差, 命令行 --tol 可覆盖
起步策略: 先写 1 道最简单的 count 题, gt 与 wren 同 SQL, 跑通 runner 后再扩题。
"""
QUESTIONS = [
    {{
        "id": "q01",
        "domain": "{domain}",
        "priority": "P0",
        "question": "TODO: 用自然语言描述这道题",
        "gt": "SELECT count(*) AS v FROM <物理表名>",
        "wren": "SELECT count(*) AS v FROM <MDL模型名>",
    }},
]
'''

RULES_TEMPLATE = '''# {domain} 业务口径

> 本文件是问数 Agent 的第一上下文: 指标公式、时间范围、易混定义必须在这里有唯一出处。

## 数据范围

- 数据快照日: TODO (显式日期, 不要依赖 current_date)
- 覆盖期间: TODO

## 核心口径

- TODO: 指标名 = 计算公式 (分子/分母写清楚, 排除项写清楚)

## 易混定义

- TODO: 容易混淆的概念辨析
'''

MODEL_TEMPLATE = '''name: {first_table}
table_reference:
  catalog: ""
  schema: {domain}
  table: {first_table}
primary_key: id
properties:
  description: "TODO: 表用途"
columns:
  - name: id
    type: INTEGER
    not_null: true
    properties:
      description: "TODO: 主键说明"
  # TODO: 参照此格式补齐全部列; 列 description 是 NL2SQL 的功能输入, 认真写
'''

WREN_PROJECT_TEMPLATE = '''# wren 语义项目清单 (wren 0.13.4)
# schema 与库文件名 stem 一致 (db/duckdb/{domain}.duckdb → schema: {domain})
# profile 需与 `wren profile add {domain}_duck ...` 创建的名称一致
schema_version: 5
name: {domain}
version: '1.0'
catalog: wren
schema: {domain}
data_source: duckdb
profile: {domain}_duck
'''

README_TEMPLATE = '''# {domain} 业务分析交付

按 semantic-analytics 技能的 ②~⑥ 阶段填充本骨架:

1. `db/seed/` 写造数脚本 (固定随机种子, 快照日显式常量)
2. `python3 <技能>/scripts/load_db.py --csv-dir db/seed/out --db db/duckdb/{domain}.duckdb`
3. `semantic/models/` 每表一个 metadata.yml; 关系写 `relationships.yml`
4. `semantic/knowledge/rules/general.md` 写业务口径
5. `python3 <技能>/scripts/run_all.py --questions validation/questions.py \\
     --project semantic --db db/duckdb/{domain}.duckdb`

注意: `semantic/wren_project.yml` 已由脚手架生成 (data_source=duckdb), 无需手工 `wren init`;
首次使用先绑定数据源 profile (`wren profile add {domain}_duck --from-file <json> \\
&& wren context set-profile {domain}_duck`), profile 的 url 指向 `db/duckdb/` 目录。
`semantic/.env` 可为空; 缺失时 runner 跳过不报错。
'''

RELATIONSHIPS_TEMPLATE = '''# 跨模型关系 (many_to_one: 从多端指向一端)
# 造数完成后按实际外键补齐; 去掉注释并按 wren 0.13.4 格式书写, 例如:
relationships: []
# relationships:
#   - name: orders_customer
#     models: [orders, customers]
#     join_type: many_to_one
#     condition: orders.customer_id = customers.customer_id
'''

def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  {path}")

def main():
    ap = argparse.ArgumentParser(description="生成新业务域骨架")
    ap.add_argument("--domain", required=True, help="域标识 (小写英文, 如 finance)")
    ap.add_argument("--root", default=".", help="项目根目录 (默认当前目录)")
    ap.add_argument("--first-table", help="主表名 (默认 <domain>_main)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    domain = args.domain.lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", domain):
        sys.exit(f"--domain 需为小写字母开头的 [a-z0-9_]: {domain}")   # 与 load_db 表名约定一致
    first_table = args.first_table or f"{domain}_main"
    base = root / domain
    if base.exists():
        sys.exit(f"目标已存在: {base}")

    print(f"生成 {domain} 域骨架 → {base}")
    templates = {
        f"db/seed/gen_{domain}_data.py": '"""TODO: {domain} 造数脚本 (固定随机种子, 快照日显式常量)"""\n',
        "db/duckdb/.gitkeep": "", "semantic/.env": "",
        "semantic/wren_project.yml": WREN_PROJECT_TEMPLATE,
        f"semantic/models/{first_table}/metadata.yml": MODEL_TEMPLATE,
        "semantic/relationships.yml": RELATIONSHIPS_TEMPLATE,
        "semantic/knowledge/rules/general.md": RULES_TEMPLATE,
        "semantic/knowledge/glossary/.gitkeep": "", "semantic/knowledge/sql/.gitkeep": "",
        "validation/questions.py": QUESTIONS_TEMPLATE, "validation/results/.gitkeep": "",
        "README.md": README_TEMPLATE,
    }
    for name, template in templates.items():
        write(base / name, template.format(domain=domain, first_table=first_table))
    print("完成: 按 README 的 1-5 步填充, 验证通过后此域即可被问数")

if __name__ == "__main__":
    main()
