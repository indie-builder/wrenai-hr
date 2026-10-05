// New cache keys keep pre-module deployments from mixing with the old client.
import { loadDashboard } from "./query-client.js?v=2";
import { renderDashboard } from "./charts.js?v=2";

const status = document.getElementById("status");
try {
  if (window.__loadErrMsg) throw new Error(window.__loadErrMsg);
  const { engine, client, manifest, spec } = await loadDashboard();
  // Public inspection hooks share the same normalized query contract as Python.
  Object.assign(window, { wrenEngine: engine, dashboardResults: client.results, dashboardQuerySpec: spec });
  document.querySelector("header .sub").textContent = `数据快照截至 ${manifest.snapshot_date}`;
  status.textContent = `✓ 已加载 ${manifest.tables.length} 张表的数据快照，正在执行 Cube 与语义 SQL 查询…`;
  status.className = "ok";
  await renderDashboard(client);
  status.textContent = `✓ 完成：数据快照 ${manifest.snapshot_date} · ${manifest.tables.length} 表 / 9 KPI / 12 图 · Cube 与语义 SQL 在浏览器内计算`;
  window.dashboardReady = true;
} catch (error) {
  window.__showLoadError(error.message);
}
