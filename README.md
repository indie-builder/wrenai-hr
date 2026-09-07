# wrenai-hr

参考 [WrenAI](https://github.com/Canner/WrenAI)（agent 驱动的 GenBI 引擎），以 HR 业务场景为例的端到端交付与验证。

**👉 交付主文档：[hr-delivery/README.md](hr-delivery/README.md)**

**项目复刻指南：[完整操作流程 README](hr-delivery/docs/replication/README.md)** — 一张 ASCII 表说明新项目初始化、数据接入、业务口径、语义建模、Agent 问数、验证和仪表盘交付的全部步骤。

- HR 演示数据库（PostgreSQL，25 张表 / 25.3 万行中文仿真数据，八大业务域）
- Wren MDL 语义层（25 模型 / 32 关系 / 6 视图 / 6 cube / 业务口径 / 语义记忆）
- 端到端验证：41 个 HR 问题双路径自动比对，41/41 PASS（P0 口径题 11/11），一键回归 `hr-delivery/validation/v2/run_all.py`
- GenBI 仪表盘本地预览：<http://127.0.0.1:8317>（`hr-delivery/wren-project/apps/hr-overview`，9 KPI + 12 图）

本地上游参考克隆位于 `vendor/WrenAI/`（未修改，不纳入本仓库）。需要阅读源码时，可自行克隆 [WrenAI](https://github.com/Canner/WrenAI)；运行本项目使用独立安装的 Wren CLI。
