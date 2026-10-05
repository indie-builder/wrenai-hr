# Vercel MCP 服务

将本项目作为只读 HR 分析工具提供给另一个服务端项目。传输使用 **MCP Streamable HTTP**，公网地址为 `https://<部署域名>/mcp`；由 Vercel 终止 TLS。每次请求均需 `Authorization: Bearer <Token>`。

当前版本使用共享 API Token，适合两个自有项目间调用。它不提供 OAuth 浏览器登录、用户身份映射或按人/部门授权；持有 Token 的客户端可以查询全部仿真数据。客户端自行调用模型生成查询，本服务负责语义规划和受限执行，不要求模型 API Key。

## 组成

- `hr_mcp/server.py`：FastAPI 入口与官方 MCP SDK 2.3.0 的装配；无状态 HTTP、JSON 响应、Token/Host/Origin 校验。
- `hr_mcp/contracts.py`：应用版本、快照日期、数据包格式与执行上限的统一定义。
- `hr_mcp/runtime.py`、`transport.py`：查询容量、就绪检查与 HTTP 认证/请求防护。
- `hr_mcp/engine.py`、`worker.py`、`cube.py`：分析 Interface、Wren 原生规划、Cube 请求校验和受限 DuckDB 查询，独立进程执行与超时控制。
- `hr_query/sql_policy.py`、`duckdb_worker.py`：MCP 与离线验证共用的 SQL 白名单和只读执行代码；包导入不加载 MCP SDK 或 DuckDB。
- `scripts/prepare_mcp.py`：构建时从确定性种子生成私有 `hr_mcp/data/`，不替换开发用数据库。
- `pyproject.toml`、`uv.lock`：独立的 Python 3.12 运行依赖，不携带 Wren CLI、embedding、Arrow 或浏览器仪表盘。
- `vercel.json`：服务入口及执行时长，排除仅用于构建的源数据和测试；`hr_query/` 随函数源码打包。
- `scripts/check_mcp.py`：使用官方客户端检验鉴权、工具发现、真实查询与写入拒绝。
- `scripts/smoke_mcp.py`：使用临时本地端口和内存 Token 启动实际 HTTP 服务，调用官方客户端后关闭；可直接复现本地与 CI 验证。

快照为 **2026-08-31**。模型与数据更新后需要重新构建和部署。构建输入保留在仓库，但最终数据库放在函数私有目录；没有静态文件路由，不能把该目录移到 Vercel `public/`。

数据包格式 v2 只包含 `public.duckdb`、`mdl.json`、`context.json` 和 `manifest.json`；共享 Python 源码保留在 `hr_query/`，不再复制进 `data/`。清单记录各数据文件哈希、确定性种子、语义定义、运行源码、构建脚本及部署配置的来源哈希，并记录应用版本、可取得的 Git 提交（无 Git 元数据时为 null）、锁文件摘要和实际直接依赖版本。已有 v1 包仅在文件完整、哈希匹配且没有额外文件时升级；合法 v2 包同样检查后替换。构建经过 staging、真实 SQL/Cube 检查和源文件复核后才替换旧包，失败时保留旧包。

MCP CI 在独立的 `.venv` 中安装语义验证工具，先执行 `check_semantics.py --build-check`，再用 `.venv-mcp` 构建并测试函数运行代码。Vercel 构建使用已提交的 `target/mdl.json`；轻量构建只核对来源在构建期间未变化，不能独立证明 YAML 与 MDL 一致。发布前需确认部署提交通过语义一致性检查；CI 必需状态、主分支保护和 Vercel 自动部署是否等待这些检查，本次重构未验证或更改。

## 本次重构验证

2026-10-05 本地执行通过：根目录 MCP/共享查询测试 51 项（其中一个测试回放全部 41 道固定 SQL）、离线验证测试 36 项、独立 Wren CLI 双路径回归 41/41，以及语义静态检查与隔离构建一致性检查。实际 HTTP 官方客户端完成认证、8 个工具、SQL/Cube 在职人数 528、写入拒绝及数据路径 404 验证。私有 v2 包包含 25 表、273,515 行；查询期间使用固定快照 2026-08-31。这些记录不代表自然语言生成准确率，也不代表 Linux CI 或本次 Vercel 公网部署已验证。

```bash
.venv-mcp/bin/python scripts/prepare_mcp.py
.venv-mcp/bin/python scripts/smoke_mcp.py
```

## 历史部署验收

以下记录对应重构前提交，不替代本次代码的 CI 和公网验收；本次重构未部署到 Vercel。

- [PR #5 的 Linux MCP CI](https://github.com/indie-builder/wrenai-hr/actions/runs/37227865181) 已通过：完整 25 表数据包构建、32 项认证/协议/引擎测试，其中包含 41 题原生语义查询与独立标准 SQL 对照，以及 Vercel 隔离依赖加载回归。
- 首版 Linux 实际安装依赖加私有服务数据包共 **302,967,160 字节**，小于项目 450 MB 预检阈值。Vercel 已成功完成实际构建；平台对依赖进行外置优化，因此 CI 安装目录体积不能直接当作最终函数体积。
- 本地官方 SDK 经真实 HTTP 完成工具发现、认证查询及写入拒绝。测试确认缺失/错误 Token 为 401，正确 Token 返回在职人数 528（快照 2026-08-31）。
- 2026-10-05 已通过 Vercel 控制台导入 GitHub 主分支并部署，生产地址为 `https://wrenai-hr-mcp.vercel.app/mcp`。生产 Token 以敏感环境变量保存；预览环境不复用生产 Token。
- [生产部署 67LwAqwAWUbq1Qcr5vtoKy4Rerda](https://vercel.com/lovemyrmbb-3480s-projects/wrenai-hr-mcp/67LwAqwAWUbq1Qcr5vtoKy4Rerda) 对应提交 `3a1205f`，已完成公网官方 SDK 验证：`/health` 200、缺失/错误 Token 401、8 个工具发现、SQL 与 Cube 均返回在职人数 528、DELETE 返回 `SQL_REJECTED`、数据库静态路径返回 404。
- 公网 MCP 固定 SQL 回归 **41/41 通过**：逐题经 HTTPS `query_sql` 查询，并与本地只读数据库标准 SQL 比对。本地证据为 `hr-demo/validation/v2/runs/mcp-production-20261005/regression.json`（不提交）。这是固定 SQL 执行对照，不代表实时自然语言生成准确率。
- 首次部署发现隔离查询进程未加载平台依赖目录，已在 [PR #5](https://github.com/indie-builder/wrenai-hr/pull/5) 修复：显式加入部署包 `_vendor` 和平台外置依赖 `/tmp/_vc_deps/lib/python3.12/site-packages`，保留 `-I` 与严格环境变量隔离。不能只用部署 Ready、健康检查或工具发现代替真实查询验收。

## 本地运行

以下命令从仓库根目录执行。使用独立环境，避免覆盖现有演示工具环境：

```bash
UV_PROJECT_ENVIRONMENT=.venv-mcp uv sync --locked
.venv-mcp/bin/python scripts/prepare_mcp.py
# 创建被 Git 忽略、权限 0600 的 .env.mcp，不打印 Token，不覆盖已有文件
.venv-mcp/bin/python scripts/create_mcp_token.py
set -a
. ./.env.mcp
set +a
.venv-mcp/bin/python -m uvicorn hr_mcp.server:app --host 127.0.0.1 --port 8320
```

另一个终端也可加载同一份由上述脚本创建的 `.env.mcp`，共享本地测试 Token。仅创建文件不会自动注入 Uvicorn，启动前需加载环境变量。不要 source 来历不明的 env 文件。环境变量说明在根 `.env.example`。

```bash
export MCP_URL=http://127.0.0.1:8320/mcp
.venv-mcp/bin/python scripts/check_mcp.py
.venv-mcp/bin/python -m unittest discover -s tests -v
```

`GET /health` 仅返回服务状态、版本和快照日，不包含业务数据。Token 缺失或配置无效、数据包未准备好时服务不能报告 ready。未携带或携带错误 Token 的 `/mcp` 请求返回 `401`；Token 配置缺失返回 `503`。

## 部署到 Vercel

1. 在已登录的 Vercel 控制台导入 GitHub 仓库，或通过 `npx vercel@latest login` / `npx vercel@latest link` 建立项目。项目 Root Directory 使用仓库根目录，Framework 选择 FastAPI。当前项目 `wrenai-hr-mcp` 绑定 GitHub `main`，合并代码后自动部署生产。
2. 在 Vercel Project Settings → Environment Variables 中添加 `MCP_AUTH_TOKEN`，使用至少 32 字符的高熵随机值；建议生成 48 个随机字节的 URL-safe Token。生产与预览使用不同 Token。不要把 Token 放进 `vercel.json`、命令行参数或客户端前端代码。
3. 默认识别 `VERCEL_URL` 和 `VERCEL_PROJECT_PRODUCTION_URL`。使用自定义域名时，把准确域名加入 `MCP_ALLOWED_HOSTS`（逗号分隔，示例 `hr-api.example.com`）。通常服务端调用不带 `Origin`，无需设置 CORS；若客户端确实发送 Origin，使用 `MCP_ALLOWED_ORIGINS` 指定完整源，例如 `https://your-app.example.com`。Origin 允许名单不等于开启跨域浏览器调用。
4. 执行 `npx vercel@latest deploy --prod`。Vercel 安装锁定依赖后运行 `python scripts/prepare_mcp.py`，生成只读数据库及语义包，随后打包函数。无需上传本地 `.venv`、DuckDB、凭据或检索缓存。
5. 检查部署日志、`/health` 和远端 MCP。若 Vercel Deployment Protection 在 MCP 前面返回登录页或平台 `401`，需为此项目的生产访问选择允许服务端调用的保护设置，或配置 Vercel 官方 Automation Bypass；该平台访问层与本服务 Bearer Token 是两层独立控制。

```bash
export MCP_URL=https://<生产域名>/mcp
# 此终端已安全配置和生产相同的 MCP_AUTH_TOKEN
.venv-mcp/bin/python scripts/check_mcp.py
```

不要把 `curl GET /mcp` 当作完整健康检查。无状态 MCP 不需要常驻 SSE 会话，带认证的 GET 或 DELETE 可能返回 `405`；正确验证方式是 MCP 客户端握手、工具发现和调用。

## 另一个项目如何接入

使用支持 Streamable HTTP 和自定义 HTTP headers 的 MCP 客户端。配置表达方式随客户端而异，核心值为：

```json
{
  "url": "https://wrenai-hr-mcp.vercel.app/mcp",
  "headers": {
    "Authorization": "Bearer <从服务端Secret读取的Token>"
  }
}
```

这是接入参数示意，Token 占位符和环境变量替换必须由具体客户端实现。不要原样当作所有客户端通用配置。若客户端只支持 OAuth、不能发送自定义 Bearer header，需要另行接入真实 OAuth 授权服务，当前版本不伪造 discovery metadata。

官方 Python MCP SDK 2.3.0 的客户端示例：

```python
import os
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

async def query_hr():
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {os.environ['HR_MCP_TOKEN']}"},
        timeout=httpx2.Timeout(connect=15, write=15, pool=15, read=60),
    ) as http:
        async with Client(streamable_http_client(os.environ['HR_MCP_URL'], http_client=http)) as client:
            tools = await client.list_tools()
            context = await client.call_tool("get_context", {})
            result = await client.call_tool("query_sql", {
                "sql": "SELECT count(*) AS headcount FROM employees WHERE status = '在职'"
            })
            return result
```

工具包括：

| 工具 | 用途 |
| --- | --- |
| `get_context` | 快照、业务口径、术语与使用限制 |
| `list_models` / `describe_model` | 模型/视图概览及字段信息 |
| `list_cubes` / `describe_cube` | 已定义指标与可用维度、筛选方式 |
| `plan_sql` | 校验语义 SQL 并检查展开计划 |
| `query_sql` | 执行经语义与物理白名单校验的 SELECT |
| `query_cube` | 查询声明的 Cube 指标，复用统一口径 |

Agent 先获取上下文和模型说明；聚合优先复用 Cube，其他分析使用 MDL 模型 SQL。错误、超时或结果超限必须作为失败处理，不能解释为零或无数据。服务不提供数据写入、上传文件、shell 或记忆存储工具。

## 运行边界

- 每实例最多同时执行 2 个规划、SQL 或 Cube 操作；额外请求排队最多 5 秒，超时返回 `QUEUE_TIMEOUT`。健康检查和模型/上下文查询使用独立容量，不占用查询名额。已启动同步查询遇到请求取消时，名额在该任务结束后才释放；调用方应限制并发并设置超时，不建议突发批量调用。DuckDB 只读并关闭文件/网络扩展访问。SQL 白名单是受信客户端的防护，不等同于多租户操作系统沙箱。
- HTTP 请求上限 64 KiB；隔离 worker 启动后每次规划/查询有 20 秒期限，不包含之前的排队和 HTTP 传输时间，不承诺 HTTP 总耗时为 20 秒。最多返回 1,000 行，输出另有字节限制。返回的非空值使用字符串以保留数值表示，`complete=true` 表示完整返回；超限返回明确错误，避免将截断结果当作完整答案。
- 无状态实例和只读快照适合当前小型仿真数据。更大的数据或频繁更新应改用独立查询后端，不能依赖函数本地文件持续写入。
- Token 在服务端环境变量中保存，轮换时更新调用方 Secret 并重新部署；当前只接受单个有效 Token。
- 本地、Linux CI、Vercel 构建、云端 MCP 调用是不同验证层；实际部署状态以部署记录为准。

参考：[Vercel FastAPI](https://vercel.com/docs/frameworks/backend/fastapi)、[Vercel Python 限制](https://vercel.com/docs/functions/limitations)、[MCP Python ASGI](https://py.sdk.modelcontextprotocol.io/run/asgi/)、[MCP Python 客户端传输](https://py.sdk.modelcontextprotocol.io/client/transports/)。
