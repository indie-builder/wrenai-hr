#!/usr/bin/env python3
"""Internal JSON stdin/stdout protocol. No command strings or shell execution.

Used for trusted GT SQL and policy-validated, Wren-planned NL SQL. Database is
mounted READ_ONLY before external access is disabled and configuration locked.
"""
import json
import sys


def query(database, sql, max_rows=10000):
    import duckdb

    con = duckdb.connect(config={"threads": 2, "memory_limit": "512MB", "temp_directory": "",
                                "allow_unsigned_extensions": "false",
                                "autoinstall_known_extensions": "false",
                                "autoload_known_extensions": "false"})
    try:
        escaped = database.replace("'", "''")
        con.execute(f"ATTACH '{escaped}' AS public (READ_ONLY)")
        con.execute("SET search_path = 'public.main'")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        statements = con.extract_statements(sql)
        if len(statements) != 1 or statements[0].type != duckdb.StatementType.SELECT:
            raise ValueError("only one SELECT is accepted")
        cursor = con.execute(sql)
        columns = [item[0] for item in cursor.description]
        rows = cursor.fetchmany(max_rows + 1)
        if len(rows) > max_rows:
            raise ValueError("row limit exceeded; query not scored")
        return {"complete": True, "columns": columns,
                "rows": [[None if value is None else str(value) for value in row] for row in rows]}
    finally:
        con.close()


def main():
    try:
        payload = json.load(sys.stdin)
        result = query(payload["database"], payload["sql"])
        sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
        return 0
    except Exception as exc:
        # Driver errors can echo connection values/SQL; retain the class only.
        sys.stderr.write(type(exc).__name__ + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
