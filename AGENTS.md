# AGENTS.md

## 项目与通用约束

本仓库是 WrenAI HR 分析演示，主要工作区为 `hr-demo/`；使用 DuckDB、Wren 语义层、Agent 问数与浏览器仪表盘。公司“星辰科技”和人员数据均为虚构。默认用中文解释业务结果与项目变更。

- 数据快照固定为 **2026-08-31**；“当前”、年龄、司龄、合同预警使用此日期。更换快照日须同步模型、规则、题库和页面并回归。
- 业务答案来自实际执行；报告结果、口径、时间范围及执行失败。历史数字与历史 PASS 只能作为记录；固定 SQL 回归不代表自然语言生成准确率。
- 业务定义以 [业务规则](hr-demo/wren-project/knowledge/rules/general.md) 为准；模型修改从 YAML 源文件开始，构建成功后再使用产物。
- `public.duckdb` 文件名固定，Wren 据此挂载 `public` catalog。`load_duckdb.sh` 会替换开发数据库，仅在首次创建或明确允许丢弃现库时运行；普通查询、文档修改和验证保留现库。
- MCP 缺失 Token 时拒绝服务；保持 Bearer Token、语义及规划后物理 SQL 两次校验、只读 worker 和资源限制，新增工具默认只读。私有数据库与业务上下文保留在 `hr_mcp/data/`，不能作为静态文件发布。
- 凭据、虚拟环境、本地数据库、`.wren/memory/` 缓存与临时运行报告保持 Git 忽略。提交前检查差异中是否混入凭据、真实个人数据或本地产物。
- `vendor/WrenAI/` 是可选的本地上游参考，不默认修改或提交。

## 按任务读取

只加载当前任务需要的入口；下表的历史和迁移资料有独立的阅读条件。

| 当前任务 | 阅读入口与定位 |
| --- | --- |
| 了解交付范围 | [根 README](README.md)，再按任务选择下方入口 |
| 初始化环境、复现 HR 演示、修复 profile | [演示 README：环境与首次初始化](hr-demo/README.md#环境与首次初始化)；分析使用 `.venv`，MCP 使用 `.venv-mcp` |
| 业务问数、修改模型或知识 | [语义项目 AGENTS](hr-demo/wren-project/AGENTS.md) 和 [业务规则](hr-demo/wren-project/knowledge/rules/general.md)；进入 `wren-project/` 时同时遵守该目录的约定 |
| 修改 MCP、共享 SQL 校验或查询执行 | [MCP 说明](hr-demo/docs/mcp-vercel.md)；`hr_mcp/` 维护传输、规划与调度，`hr_query/` 维护共享 SQL policy 与只读执行，`tests/` 验证公开调用行为 |
| 修改仪表盘、同步页面数据 | [演示 README：仪表盘](hr-demo/README.md#仪表盘快照与本地预览)；需求修改 [query-spec.json](hr-demo/wren-project/apps/hr-overview/query-spec.json)，导出与核对使用 [export_dashboard.py](hr-demo/scripts/export_dashboard.py) |
| 修改数据种子或装载过程 | [演示 README：数据与初始化](hr-demo/README.md#数据与查询路径)，再定位 `hr-demo/db/`；任一种子来源清单校验失败时保留原库并报错 |
| 修改固定 SQL 回归或自然语言评测 | [验证入口](hr-demo/validation/v2/README.md)；题库与标准答案在 [questions.py](hr-demo/validation/v2/questions.py)，运行状态由当次报告判断 |
| 修改文档、检查导航 | [演示 README：文档检查](hr-demo/README.md#文档检查)；`python3 scripts/check_docs.py`，纯文档修改无需全量数据库回归 |
| 迁移到新的业务项目 | [复刻指南](hr-demo/docs/replication/README.md)，示例使用新项目 PostgreSQL；复现本 HR 项目使用演示 README |
| 追溯历史目标或验收 | [GOAL.md](hr-demo/GOAL.md)、[验证记录](hr-demo/validation/v2/matrix_v2.md)、[旧矩阵](hr-demo/validation/matrix.md)；先确认日期、提交及验证层次 |

## 执行与交付

- 根目录命令从仓库根运行；Wren 语义命令在 `hr-demo/wren-project/` 的子 Shell 中运行。环境和 profile 的建立步骤见演示 README，避免复制其他项目的虚拟环境。
- 语义变更后执行 validate、build、index 和受影响回归；共享模型、关系与指标口径修改后执行全量回归。仪表盘受影响时同步导出并校验；MCP 发布前核对部署提交的语义构建检查。
- 合并 PR 前确认分支基于最新 `main`：CI 构建合并结果，Vercel 预览构建分支自身快照；旧基线分支出现"CI 通过但预览失败"时先变基再重查，不能只看 CI 通过。
- 文件预检、SQL 对照、自然语言评测、本地 HTTP、Linux CI 和公网调用分别报告；完成时说明修改文件、实际验证与未验证范围。
- 大输出先限定文件、节或条目；检索完整输出保存后分段读取。续接摘要保留已读文件、已加载技能、验证结果和待办，未变内容沿用已有结果。依赖诊断从当前任务的依赖声明取得发行包名，导入名和第三方源码位置从当前解释器确认后再枚举文件。
