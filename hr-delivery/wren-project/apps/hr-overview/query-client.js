// Shared query inputs live in query-spec.json; no metric SQL in the chart layer.
export function normalizeRows(rows, query) {
  const result = rows.map((row) => query.fields
    ? Object.fromEntries(Object.entries(query.fields).map(([alias, rule]) => {
      if (typeof rule === "string") {
        if (!(rule in row)) throw new Error(`查询结果缺少字段 ${rule}`);
        return [alias, row[rule]];
      }
      if (!(rule.field in row) || (rule.divide_by && !(rule.divide_by in row))) {
        throw new Error(`查询结果缺少字段 ${rule.field}`);
      }
      const divisor = rule.divide_by ? row[rule.divide_by] : (rule.divide ?? 1);
      let value = row[rule.field] == null || divisor == null || Number(divisor) === 0
        ? null : Number(row[rule.field]) * (rule.multiply ?? 1) / Number(divisor);
      if (value != null && rule.round != null) {
        const factor = 10 ** rule.round;
        value = Math.sign(value) * Math.round(Math.abs(value) * factor) / factor;
      }
      if (value != null && !Number.isFinite(value)) throw new Error(`非有限指标 ${alias}`);
      return [alias, value];
    })) : { ...row });
  result.sort((a, b) => {
    for (const { field, direction } of query.sort || []) {
      const x = a[field], y = b[field];
      if (x == null && y == null) continue;
      if (x == null) return 1;
      if (y == null) return -1;
      const cmp = typeof x === "number" && typeof y === "number"
        ? x - y : (String(x) < String(y) ? -1 : String(x) > String(y) ? 1 : 0);
      if (cmp) return direction === "desc" ? -cmp : cmp;
    }
    return 0;
  });
  return result;
}

export function createQueryClient(engine, spec) {
  const results = {};
  return {
    results,
    async query(id) {
      const query = spec.queries[id];
      if (!query) throw new Error(`未知仪表盘查询 ${id}`);
      const raw = query.cube ? await engine.cubeQuery(query.cube) : await engine.query(query.sql);
      if (!raw.length) throw new Error(`查询 ${id} 未返回数据`);
      const rows = normalizeRows(raw, query);
      results[id] = rows;
      return rows;
    },
  };
}
