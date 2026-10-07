# wrenai-hr

参考 [WrenAI](https://github.com/Canner/WrenAI) 当前 Agent 驱动的 GenBI 架构，以虚构公司“星辰科技”的 HR 数据演示语义问数、固定 SQL 回归与浏览器仪表盘。数据快照日为 **2026-08-31**。

`hr-demo/` 是 HR 分析演示工作区，集中维护数据种子、Wren 语义定义、仪表盘、回归题库和复现文档。MCP 构建从这里读取数据与业务定义，运行时使用 `hr_mcp/data/` 中的私有数据包。

- 按任务查找入口：[AGENTS.md 路由表](AGENTS.md#按任务读取)。
- 常用入口：[初始化与复现](hr-demo/README.md#环境与首次初始化)、[验证入口](hr-demo/validation/v2/README.md)、[MCP 服务](hr-demo/docs/mcp-vercel.md)。

数据层为 DuckDB 单文件，25 张表、273,515 行仿真数据，覆盖八类 HR 业务。语义层包括 25 个模型、32 条关系、6 个视图、6 个 Cube，以及业务规则和查询示例。

仪表盘保留 9 个 KPI 和 12 个图表区域，从共享查询配置读取语义 SQL 或 Cube 请求，在浏览器中计算导出的必要 Parquet 数据。数据库种子与页面快照独立；更新后通过导出脚本同步，而非依赖页面自动刷新。

41 道固定 SQL 回归比较“直接查询物理表”与“经 Wren 语义层执行”的结果。自然语言评测单独保存 Agent 生成的 SQL 与执行记录；**固定 SQL 通过率不代表自然语言生成准确率**。当前验证状态以实际运行输出和验证记录为准。

在按 [环境与首次初始化](hr-demo/README.md#环境与首次初始化) 完成初始化后，回归与评测命令见 [验证入口](hr-demo/validation/v2/README.md)，仪表盘导出与本地预览步骤见 [仪表盘快照与本地预览](hr-demo/README.md#仪表盘快照与本地预览)。

只读 MCP 服务的调用链、私有数据包构建与部署方法见 [MCP 文档](hr-demo/docs/mcp-vercel.md)。仓库分层职责与按任务的完整入口见 [AGENTS.md](AGENTS.md)；实际部署状态以部署记录为准。

全部数据均为仿真数据；接入、授权与数据访问边界见 [交付范围](hr-demo/README.md#交付范围)。

本地上游参考克隆 `vendor/WrenAI/` 不纳入仓库；运行时使用独立安装的 Wren CLI，关键依赖版本见 [pyproject.toml](pyproject.toml) 与完整锁文件 [uv.lock](uv.lock)。

Python 代码安装为一个 `src` 发行包。`hr_contracts` 维护纯结果契约，`hr_query` 维护安全执行，`hr_analytics` 维护离线运行，`hr_mcp` 维护服务。统一验证运行 `python3 scripts/verify.py docs analysis data mcp installation`；首次检出使用 `--fresh-data` 构建临时验证库，不替换开发库。
