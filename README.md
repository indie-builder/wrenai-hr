# wrenai-hr

参考 [WrenAI](https://github.com/Canner/WrenAI) 当前 Agent 驱动的 GenBI 架构，以虚构公司“星辰科技”的 HR 数据演示语义问数、固定 SQL 回归与浏览器仪表盘。数据快照日为 **2026-08-31**。

`hr-demo/` 是 HR 分析演示工作区，集中维护数据种子、Wren 语义定义、仪表盘、回归题库和复现文档。MCP 构建从这里读取数据与业务定义，运行时使用 `hr_mcp/data/` 中的私有数据包。

- [演示说明与复现命令](hr-demo/README.md)
- [迁移到新业务的操作指南](hr-demo/docs/replication/README.md)
- [Agent 工作约定](AGENTS.md)
- [验证与问题处理记录](hr-demo/validation/v2/matrix_v2.md)
- [Vercel MCP 服务与 Token 接入](hr-demo/docs/mcp-vercel.md)

数据层为 DuckDB 单文件，25 张表、273,515 行仿真数据，覆盖八类 HR 业务。语义层包括 25 个模型、32 条关系、6 个视图、6 个 Cube，以及业务规则和查询示例。

仪表盘保留 9 个 KPI 和 12 个图表区域，从共享查询配置读取语义 SQL 或 Cube 请求，在浏览器中计算导出的必要 Parquet 数据。数据库种子与页面快照独立；更新后通过导出脚本同步，而非依赖页面自动刷新。

41 道固定 SQL 回归比较“直接查询物理表”与“经 Wren 语义层执行”的结果。自然语言评测单独保存 Agent 生成的 SQL 与执行记录；**固定 SQL 通过率不代表自然语言生成准确率**。当前验证状态以实际运行输出和验证记录为准。

```bash
# 在已按交付说明完成环境、数据库和 profile 初始化后
python3 hr-demo/validation/v2/run_all.py
.venv/bin/python hr-demo/scripts/export_dashboard.py --check
python3 -m http.server 8317 --bind 127.0.0.1 \
  --directory hr-demo/wren-project/apps/hr-overview
```

打开 <http://127.0.0.1:8317>。另提供带 Bearer Token 的只读 MCP Streamable HTTP 服务：`hr_mcp/` 负责传输与语义规划，`hr_query/` 提供 MCP 和离线验证共用的 SQL 校验与只读执行，`scripts/prepare_mcp.py` 构建私有数据包。Vercel 构建配置与接入方法见上述服务文档，实际部署状态以部署记录为准。

全部数据均为仿真数据；本项目未实现真实 HR 系统接入、审批写入或按用户/部门授权。MCP Token 允许访问整份仿真数据，浏览器计算也不等于数据访问控制。外部 Agent 的联网方式由其运行环境决定。

本地上游参考克隆 `vendor/WrenAI/` 不纳入仓库；运行时使用独立安装的 Wren CLI，关键依赖版本见 [requirements-demo.txt](requirements-demo.txt)。
