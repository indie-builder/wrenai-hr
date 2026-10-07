"""Fail-closed SQL policy shared by MCP and offline evaluation (sqlglot 30.18.0).

Only explicitly supported relational syntax/functions are accepted. Validate
semantic SQL before planning and physical SQL after expansion, then execute with
the read-only DuckDB worker. This guard is not an operating-system sandbox.
"""
import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

MAX_SQL_CHARS = 50000
MAX_AST_NODES = 5000

# Do not replace with a denylist: unknown extension functions must remain blocked.
SAFE_NODES = frozenset("""
select union intersect except subquery cte with from join table tablealias
identifier column alias literal null boolean star paren distinct
where group having order ordered limit offset qualify window windowspec filter
add sub mul div intdiv mod neg pow eq neq gt gte lt lte and or not is in between
like ilike escape case if cast trycast datatype datatypeparam interval var
count sum avg min max round abs ceil floor coalesce nullif greatest least
extract datediff dateadd datesub datetrunc timestamptrunc tsordstodate
strtodate timetostr year month day lastday datefromparts lower upper length trim ltrim rtrim
substring concat concatws replace splitpart rownumber rank denserank lag lead
firstvalue lastvalue percentrank cumedist ntile stddev stddevpop variance
anonymous
""".split())
# SQLGlot represents the confirmed DuckDB MAKE_DATE constructor as DateFromParts.
# Some ordinary built-ins remain anonymous; caller-defined macros stay blocked.
SAFE_ANONYMOUS = frozenset({"DATE_PART"})
SAFE_TYPES = frozenset({"BOOLEAN", "TINYINT", "SMALLINT", "INT", "BIGINT", "HUGEINT",
                        "UTINYINT", "USMALLINT", "UINT", "UBIGINT", "FLOAT", "DOUBLE",
                        "DECIMAL", "VARCHAR", "CHAR", "TEXT", "DATE", "TIMESTAMP",
                        "TIMESTAMPTZ", "TIME", "INTERVAL"})


class PolicyError(ValueError):
    pass


def validate_sql(sql, allowed_tables, *, physical=False):
    if not isinstance(sql, str) or not sql.strip() or len(sql) > MAX_SQL_CHARS:
        raise PolicyError(f"SQL必须为非空字符串且不超过{MAX_SQL_CHARS}字符")
    try:
        statements = sqlglot.parse(sql, read="duckdb", error_level=sqlglot.ErrorLevel.RAISE)
    except (sqlglot.errors.SqlglotError, RecursionError) as exc:
        raise PolicyError("SQL解析失败") from exc
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation)):
        raise PolicyError("仅允许一条SELECT查询（可含CTE/集合查询）")
    tree = statements[0]
    nodes = list(tree.walk())
    if len(nodes) > MAX_AST_NODES:
        raise PolicyError("SQL语法树超过限制")
    for node in nodes:
        if node.key not in SAFE_NODES:
            raise PolicyError(f"不支持的SQL语法: {node.key}")
        if isinstance(node, exp.Anonymous) and node.name.upper() not in SAFE_ANONYMOUS:
            raise PolicyError("函数不在允许列表")
        if isinstance(node, exp.DataType) and node.this.value not in SAFE_TYPES:
            raise PolicyError("数据类型不在允许列表")
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise PolicyError("不允许递归CTE")
        if isinstance(node, exp.Star) and any(node.args.values()):
            raise PolicyError("不支持扩展星号语法")
        if isinstance(node, exp.Table):
            if not isinstance(node.this, exp.Identifier):
                raise PolicyError("不允许文件名扫描或表函数")
            if node.this.quoted and any(c in node.name for c in "/\\:"):
                raise PolicyError("不允许文件名扫描")
            qualifiers = (node.catalog.lower(), node.db.lower())
            permitted = {("", ""), ("", "public"), ("wren", "public")}
            if physical:
                permitted = {("", ""), ("", "public"), ("public", "main")}
            if qualifiers not in permitted:
                raise PolicyError("不允许访问外部catalog/schema")
    allowed = {name.lower() for name in allowed_tables}
    try:
        scopes = traverse_scope(tree)
        for scope in scopes:
            for source in scope.sources.values():
                if isinstance(source, Scope):
                    continue
                if not isinstance(source, exp.Table) or source.name.lower() not in allowed:
                    raise PolicyError("表不在MDL允许列表")
    except (sqlglot.errors.SqlglotError, RecursionError) as exc:
        raise PolicyError("SQL作用域解析失败") from exc
    # Re-serialize the validated AST, so the downstream parser executes the
    # representation checked here instead of raw dialect/parser edge cases.
    return tree.sql(dialect="duckdb", comments=False)


def mdl_tables(manifest):
    semantic = {item["name"] for kind in ("models", "views") for item in manifest.get(kind, [])}
    physical = set()
    for model in manifest.get("models", []):
        reference = model.get("tableReference", {})
        if reference.get("table"):
            physical.add(reference["table"])
    if not semantic or not physical:
        raise PolicyError("MDL没有可用模型/物理表")
    return semantic, physical
