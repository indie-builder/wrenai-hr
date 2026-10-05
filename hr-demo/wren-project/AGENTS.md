# AGENTS.md

This project uses [Wren Engine](https://github.com/Canner/WrenAI) as the semantic layer for data querying. Queries are written against MDL model names, not raw database tables.

## Answering data questions

When the user asks about data, metrics, reports, or business questions, follow this workflow:

1. First query: read `wren skills get usage` and `wren context instructions`; reload rules after changes.
2. `wren memory fetch -q "<question>"` — get relevant schema context.
3. `wren memory recall -q "<question>" --limit 3` — find similar past queries; verify dates, filters and ranking before reuse.
4. For aggregations, run `wren cube list` and `wren cube describe <name>`. Prefer `wren cube query` when the declared measures and dimensions cover the question; inspect `--sql-only` first. Use modeled SQL for other questions.
5. `wren dry-plan --sql "<sql>"`, then `wren query --sql "<sql>"` — validate and execute through the semantic layer.
6. Store a confirmed SQL answer with `wren memory store --nl "<question>" --sql "<sql>"`; inspect the written file and recall it again. For a Cube answer, obtain its SQL with `--sql-only`, verify that SQL, then store it.

Report the actual result, calculation, period and limitations. Execution errors are not zero or empty results. This delivery uses **2026-08-31** as its fixed snapshot date; do not substitute the runtime date. Historical departmental metrics use the current employee department unless the query explicitly reconstructs transfers.

## Modifying the data model

When the user wants to add models, change schema, or onboard a new table:

1. Edit YAML files in `models/`, `views/`, or `relationships.yml`
2. `wren context validate` — check structure
3. `wren context build` — compile to `target/mdl.json`
4. `wren memory index` — re-index schema for search

## Capturing business context

Rules the schema can't express — canonical tables, default filters, units, enum meanings — go in `knowledge/rules/*.md` (read by `wren context instructions`). Confirmed NL→SQL examples are saved with `wren memory store` (step 5 above), which writes them to `knowledge/sql/`. Both live in the project and are committed with it.

## Prerequisites

This project requires the `wren` CLI. The DuckDB data source ships in wrenai core, so the base install is enough for this project:

```bash
pip install -r ../../requirements-demo.txt
pip install 'wrenai[memory]==0.13.4'
```

The project binds profile `hr_demo_duck` (datasource `duckdb`, directory `../db/duckdb/` relative to this project). For other data sources install the matching connector extra. The optional `memory` extra enables embedding retrieval; its derived index is not committed. Keep credentials in local profiles, outside the repository.

See https://docs.getwren.ai/oss/get_started/installation for full setup. Match CLI workflow guides to the installed version with `wren skills get <name>`.

## Quick reference

| Task | Command |
|------|---------|
| Run a query | `wren --sql "SELECT ..."` |
| Preview planned SQL | `wren dry-plan --sql "SELECT ..."` |
| Show available models | `wren context show` |
| Check connection | `wren profile debug` |
| Check memory index | `wren memory status` |
| Rebuild after changes | `wren context build && wren memory index` |
