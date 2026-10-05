#!/usr/bin/env bash
# 构建 DuckDB 版 HR 演示库 (唯一数据层, 无 Docker 依赖)
# 产物: duckdb/public.duckdb —— 文件名固定, wren 按文件名挂载 catalog
# 前提: seed/manifest.json 校验仓库内 25 表的 typed Parquet 种子
set -euo pipefail
cd "$(dirname "$0")"

VENV_PY=../../.venv/bin/python
if [ -x "$VENV_PY" ]; then PY="$VENV_PY"; else PY=python3; fi

"$PY" build_duckdb.py "$@"
