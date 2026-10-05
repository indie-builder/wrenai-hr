// Shared query inputs live in query-spec.json; no metric SQL in the chart layer.
async function fetchAsset(path, binary = false) {
  let response;
  try {
    response = await fetch(path, { cache: "no-cache" });
  } catch {
    throw new Error(`无法读取快照文件 ${path}，请检查本地服务和网络后刷新；数据更新后请重新导出快照。`);
  }
  if (!response.ok) throw new Error(`快照文件加载失败: ${path} (${response.status})，请重新导出快照后刷新。`);
  return binary ? response.arrayBuffer() : response.json();
}

export async function loadDashboard() {
  const [mdl, spec, manifest] = await Promise.all(
    ["mdl.json", "query-spec.json", "snapshot-manifest.json"].map((path) => fetchAsset(`./${path}`)));
  if (manifest.snapshot_date !== spec.snapshot_date) throw new Error("查询配置与数据快照日期不一致，请重新导出快照");
  const { WrenEngine } = await import(`https://unpkg.com/@wrenai/wren-core-wasm@${spec.sdk_version}/dist/index.js`);
  if (typeof WrenEngine.prototype.cubeQuery !== "function") throw new Error("语义引擎版本缺少 Cube 查询接口");
  const engine = await WrenEngine.init();
  for (const table of manifest.tables) await engine.registerParquet(table.name, await fetchAsset(table.file, true));
  await engine.loadMDL(mdl, { source: "./data/" });
  return { engine, client: createQueryClient(engine, spec), spec, manifest };
}

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
