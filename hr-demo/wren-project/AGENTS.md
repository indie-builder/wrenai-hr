# Wren 语义项目工作流

本目录使用 Wren Engine 规划查询；SQL 使用 MDL 模型名。业务口径以 [general.md](knowledge/rules/general.md) 为准。环境与 `hr_demo_duck` profile 初始化见 [演示 README](../README.md#环境与首次初始化)。

下面的 Wren 命令在本目录运行，使用根目录分析环境 `../../.venv/bin/wren`；从仓库根调用时用子 Shell 切换目录。首次使用按已安装版本读取 `wren skills get usage`，需要其他子命令时查对应 `--help`。

## 回答业务数据问题

1. 每个查询会话首次读取工具约定与业务规则；规则修改后重新读取：

   ```bash
   ../../.venv/bin/wren skills get usage
   ../../.venv/bin/wren context instructions
   ```

2. 分别获取 schema 和已确认示例，核对日期、过滤条件、粒度与排序后复用：

   ```bash
   ../../.venv/bin/wren memory fetch -q "用户问题"
   ../../.venv/bin/wren memory recall -q "用户问题" --limit 3
   ```

   若 schema 输出过大，将**完整结果**保存到仓库外临时文件，再按模型或节分段读取；保留读取状态直至所需模型、关联与字段已确认。instructions、fetch、recall 分开调用，便于识别截断和执行失败。Wren 0.15.0 的 `recall` 直接以 `knowledge/sql/` 为索引（grep 后端，始终同步）；`fetch` 按规模选择 full 或 search 策略（`--threshold` 控制），`--model`、`--type` 只作用于 search 策略。使用搜索策略前核对 `memory fetch --help` 与可选 memory 依赖。

3. 聚合问题先检查 `cube list` 和 `cube describe <name>`；已有指标及维度覆盖时优先 `cube query`，用 `--sql-only` 检查 SQL。其他问题编写 MDL SQL，依次验证并执行：

   ```bash
   ../../.venv/bin/wren dry-plan --sql "根据上下文编写的 SQL"
   ../../.venv/bin/wren query --sql "根据上下文编写的 SQL" -o csv -q
   ```

   直接查询物理表仅用于标准答案核对或明确的数据诊断。`wren ask` 只生成 Agent 提示词，不会调用模型给出答案。

4. 回答包含实际结果、计算口径、数据时间范围与限制。执行失败应说明失败阶段，不能解释为零或无数据。缺少数据或定义时说明具体缺口。
5. 确认答案后保存 SQL，检查实际文件内容、是否覆盖了已有示例，并再次召回：

   ```bash
   ../../.venv/bin/wren memory store --nl "用户问题" --sql "已验证的 SQL"
   ../../.venv/bin/wren memory recall -q "用户问题" --limit 3
   ```

   Cube 答案先取得 `--sql-only` 的 SQL 并验证后存储。历史曾出现中文文件名异常，调用成功不能代替文件核对。知识改名后执行 `memory check`；若存在无源文件的历史索引，按 [演示 README 的索引恢复流程](../README.md#构建问数与更新) 备份缓存后 reset/index/check，保留知识源文件。

## 修改模型、规则与知识

1. 修改 `models/`、`views/`、`cubes/`、`relationships.yml` 的 YAML，或 `knowledge/rules/` 的业务规则；确认的 NL→SQL 示例写入 `knowledge/sql/`。`target/mdl.json` 由构建生成。
2. 在本目录依次执行；失败先修正再使用构建结果：

   ```bash
   ../../.venv/bin/wren context validate
   ../../.venv/bin/wren context build
   ../../.venv/bin/wren memory index
   ```

3. 按 [验证入口](../validation/v2/README.md) 回归受影响问题；共享模型、关系或指标口径变更执行全量回归。仪表盘受影响时按 [快照流程](../README.md#仪表盘快照与本地预览) 同步 MDL、数据和查询并核对数字。MCP 发布前核对同一部署提交的语义构建一致性检查。

## 已知限制

结合当前版本实际重现后处理，不将历史绕行直接推广到其他业务：

- 部分 DECIMAL 规划退化在本项目以 DOUBLE 绕行；精确金额场景须另行验证。
- 规范 YAML 是权威来源；完整 `target/mdl.json` 为忽略的本地构建缓存。MCP 直接编译 YAML；`check_semantics.py --build-check` 比较轻量编译、Wren 官方构建及存在的缓存。
- 现有视图主要执行关联投影，算术放在查询或 Cube 层，避开已记录的规划问题。
- 所有“当前”与派生日期使用快照 **2026-08-31**；完整业务口径（含历史部门归属、离职率分母等）以 [业务规则](knowledge/rules/general.md) 与 [演示 README·业务口径与限制](../README.md#业务口径与限制) 为准，不在本文件复述。
