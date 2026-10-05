# WrenAI HR 工程实践研究

研究日期：2026-10-05。

范围：为 Python、DuckDB、Wren 语义层和 HTML 仪表盘组成的演示 monorepo 提供结构评审依据。仅研究官方文档；没有执行本地全仓评审、测试或部署。下文区分官方事实与针对本项目的建议，不能据此认定仓库存在缺陷或某个 PR 已通过验证。

## 布局按安装与复用需求选择

**官方事实。** PyPA 同时讨论 flat layout 与 src layout：前者把可导入代码放在项目根目录，后者移到子目录；src 通常需要先安装，能够减少工作目录中的同名包遮蔽已安装包，以及可编辑安装意外暴露其他根目录文件的问题。文档没有要求所有应用采用 src。[S1] pytest 对新项目推荐 `importlib` 导入模式，并强烈建议 src 布局，尤其使用默认 `prepend` 模式时；这是测试导入实践建议。[S5]

**项目建议。** 应用可以采用 flat layout；不应仅因根目录有 Python 包而要求迁移到 src。只有需要验证安装产物、发现导入遮蔽，或形成可复用包时，再衡量 src 的收益与迁移成本。演示数据、语义 YAML 和 HTML 的职责分区也不自动意味着它们应成为 Python 包。

## 分别锁定运行与分析环境，提供统一复现入口

**官方事实。** uv 的 `pyproject.toml` 声明项目元数据与依赖要求；`uv.lock` 保存解析出的精确版本，并支持不同操作系统、架构和 Python 版本。官方建议将锁文件纳入版本控制。[S4] `uv run` 和 `uv sync` 默认按需更新锁并同步环境；`--locked` 会检查锁与项目元数据是否一致并拒绝更新，`--frozen` 跳过一致性检查。`uv lock --check` 可检查锁是否最新。[S3]

**项目建议。** MCP 生产运行依赖与 DuckDB/Wren 分析依赖可以隔离，但两者都应记录完整依赖解析结果、Python 和工具版本。仅固定直接依赖不能固定所有传递依赖。增加一个明确的 `verify` 入口，协调两个环境的安装检查、语义校验、SQL 回归与快照检查；入口可以是脚本或任务工具，不必统一虚拟环境。CI 宜显式检查锁一致性，避免把 `--frozen` 当成锁未过期的证明。

## 真实 Python 包协作出现后，再采用 workspace

**官方事实。** uv workspace 中每个成员有自己的 `pyproject.toml`，共享一个 `uv.lock`；`uv run` 和 `uv sync` 默认针对根项目，也可使用 `--package` 选择成员。依赖 workspace 成员需要声明依赖，并在 `tool.uv.sources` 中指定 `workspace = true`。成员 Python 要求取交集；需要独立环境或依赖有冲突的项目可能更适合独立项目与路径依赖。[S2]

**项目建议。** 当服务、数据构建器或验证工具实际通过公共 Python 包复用代码，且依赖兼容时，workspace 才有明确收益。多个目录、多个 workflow 或多种交付物不足以证明需要 workspace。优先比较“两套独立锁定环境”和“共享 workspace”对本项目的实际成本。

## 薄 CLI 调用稳定接口，源文件与产物通过 manifest 关联

**官方事实。** PyPA 的布局讨论强调源码导入与已安装代码之间的边界；GitHub 的 workflow artifacts 用于保存和传递构建或测试生成的文件。[S1][S7] 这两份文档没有规定 CLI 必须薄，也没有规定业务快照 manifest 的具体字段。

**项目建议。** CLI 负责参数、调用和退出码，数据构建、语义校验及报告生成通过可导入的稳定接口实现。明确 YAML/规则/查询配置是源文件，MDL、数据库、Parquet 和回归输出是各自构建或验证产物；版本管理是否保留产物，应按交付需要决定。manifest 可记录源 commit、快照日期、生成命令、工具版本及文件哈希，便于追踪“哪个源生成了哪个产物”。这属于工程建议，不是上述官方文档强制的架构。

## 集成测试从公共接口验证行为，区分不同证据

**官方事实。** pytest 文档说明导入方式和安装方式影响测试实际使用哪份代码，并建议通过隔离环境验证安装后的包。[S5] Playwright 建议测试用户可见行为，保持测试隔离，并使用会自动等待、重试的 web-first assertions。[S8]

**项目建议。** Python 集成测试可通过公开函数、CLI、HTTP/MCP 客户端验证输入、结果及失败行为，减少对私有模块拆分或内部调用顺序的依赖。无需为了采用这些原则而从 unittest 迁移到 pytest。固定 SQL 双路径回归、自然语言生成评测、认证与工具调用、浏览器实际 WASM 执行分别提供不同证据，应分别报告。测试 SQL 正确不等于自然语言生成正确，文件预检通过也不等于浏览器执行成功。该分层是本项目建议，pytest 文档没有强制“只做接口集成测试”。

## CI 复用步骤，发布绑定已验证的 commit 与产物

**官方事实。** GitHub reusable workflows 通过 `workflow_call` 暴露输入和 secrets，由调用方在 job 的 `uses` 引用。本仓库的 `./.github/workflows/...` 引用使用调用方同一 commit；跨仓库可指定 SHA、tag 或 branch，官方认为 commit SHA 对安全性与稳定性最稳妥。[S6] workflow 可用 `upload-artifact`、`download-artifact` 共享产物，依赖 job 用 `needs` 保证顺序。v4 artifacts 不可变；下载会验证 digest，但文档描述的不匹配行为是警告。跨 run 下载需要 token 和 run ID。[S7]

**项目建议。** 先复用重复的环境准备、语义校验和报告上传；复用步骤不要求合并运行与分析环境。构建、验证、发布应关联同一 source commit；能够直接使用已验证产物时，优先传递它。若平台必须重新构建，明确绑定 commit 并核对 manifest，不要假定再次从可移动 branch 构建就等于发布已验证产物。Artifact digest 警告不能被描述为必然阻止发布。产物传递与锁定依赖也不能单独证明构建已实现字节级可复现。

## 固定前端外部依赖，保留真实浏览器执行检查

**官方事实。** Playwright 建议避免测试不可控制的第三方服务，允许通过网络拦截模拟所需响应；同时要求关注用户可见行为、测试隔离和自动等待断言。[S8] 本次读取的 Playwright 文档没有提出“CDN 必须精确版本固定”的要求。

**项目建议。** HTML 仪表盘的 ECharts、Wren WASM 和其他 CDN 资源应使用明确版本，并记录升级及验证范围；此项是依赖复现建议。确定性功能测试可模拟不可控服务；另保留小规模真实加载检查，实际运行 WASM、读取 Parquet、执行页面查询，并核对图表、加载错误与关键数字。两种测试分别报告，浏览器行为成功不能代替业务口径验证。网络不可用时应记录未验证范围，不能把真实加载检查改成 mock 后仍报告为公网资源验证。

## 主评审提供的本地观察

以下由主评审 agent 提供，本研究未读取这些文件复核。它们仅用于连接研究与后续评审，不作为本次独立确认的缺陷结论。

- `requirements-demo.txt:1–8`：分析环境固定直接依赖，未提供完整传递依赖锁；对应“分别锁定运行与分析环境”的建议。
- `.github/workflows/hr-demo.yml:27–35`、`.github/workflows/mcp.yml:25–32`：两条 workflow 分别使用 pip/uv 准备分析环境，并重复语义校验；对应统一验证入口及按需复用 CI 步骤的建议。
- `pyproject.toml:18–25`：根 MCP 项目使用 `package = false`，并有 `uv.lock`；此配置本身不足以要求采用 src 或 workspace。本次查阅的 uv layout 页面未解释 `package = false` 的语义，因此不对该选项作额外官方事实断言。
- 主评审另报告尚无 Makefile/just/task/nox 统一入口及 ruff/mypy 配置；没有提供定位行号，本研究也未检查。此项仅保留为待主评审确认的上下文，不纳入官方事实。

## 实际查阅来源

所有来源于 2026-10-05 通过网页工具读取针对性摘要。八次读取均成功，没有读取未列出的链接，也没有本地执行验证。网页摘要范围有限；正文已注明来源未覆盖的事项。

- **[S1] PyPA — src layout vs flat layout**：https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/
- **[S2] Astral uv — Workspaces**：https://docs.astral.sh/uv/concepts/projects/workspaces/
- **[S3] Astral uv — Locking and syncing**：https://docs.astral.sh/uv/concepts/projects/sync/
- **[S4] Astral uv — Project structure**：https://docs.astral.sh/uv/concepts/projects/layout/
- **[S5] pytest — Good Integration Practices**：https://docs.pytest.org/en/stable/explanation/goodpractices.html
- **[S6] GitHub — Reusing workflows**：https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows
- **[S7] GitHub — Storing and sharing data from a workflow**：https://docs.github.com/en/actions/how-tos/writing-workflows/choosing-what-your-workflow-does/storing-and-sharing-data-from-a-workflow
- **[S8] Playwright — Best Practices**：https://playwright.dev/docs/best-practices
