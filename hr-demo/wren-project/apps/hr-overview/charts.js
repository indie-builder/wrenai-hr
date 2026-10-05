// Presentation registry: metric SQL and normalization belong to the query client.
const MUTED = "#8a97b0", PANEL = "#161e2e", SPLIT = "#1c2536";
const AXIS = {
  xAxis: { type: "category", axisLine: { lineStyle: { color: "#26304a" } }, axisLabel: { color: MUTED } },
  yAxis: { type: "value", splitLine: { lineStyle: { color: SPLIT } }, axisLabel: { color: MUTED } },
};
const base = {
  textStyle: { color: MUTED, fontFamily: "inherit" },
  tooltip: { backgroundColor: "#1b2437", borderColor: "#26304a", textStyle: { color: "#e8edf7" } },
  grid: { left: 8, right: 18, top: 28, bottom: 8, containLabel: true },
};
const values = (rows, key = "v") => rows.map((row) => row[key]);
const pieData = (rows) => rows.map((row) => ({ name: row.k, value: row.v }));
const WAN = (value) => (value / 10000).toLocaleString("zh-CN") + "万";
const rotated = { interval: 0, rotate: 30 };
const series = (data, color, extra = {}) => ({ data, color, ...extra });
const vBar = (data, items, label = {}, y = {}) => ({ ...AXIS,
  xAxis: { ...AXIS.xAxis, data: values(data, "k"), axisLabel: { color: MUTED, ...label } },
  yAxis: { ...AXIS.yAxis, ...y },
  series: items.map((item) => ({ name: item.name, type: "bar", data: item.data, barMaxWidth: item.max || 26,
    itemStyle: { borderRadius: item.br || [6, 6, 0, 0], color: item.color } })),
});
const hBar = (data, color, formatter, bars = values(data)) => ({ ...AXIS,
  yAxis: { type: "category", data: values(data, "k"), axisLabel: { color: MUTED } },
  xAxis: { type: "value", splitLine: { lineStyle: { color: SPLIT } }, axisLabel: { color: MUTED, formatter } },
  series: [{ type: "bar", data: bars, barMaxWidth: 16, itemStyle: { borderRadius: [0, 6, 6, 0], color } }],
});
const pie = (data, formatter) => ({
  series: [{ type: "pie", radius: ["42%", "70%"], center: ["50%", "52%"], data: pieData(data),
    label: { color: MUTED, formatter }, itemStyle: { borderColor: PANEL, borderWidth: 2 } }],
});
const dualPie = (titles, data, { fs = 11, fmts = ["{b}", "{b}"] } = {}) => ({
  title: titles.map((text, i) => ({ text, left: i ? "75%" : "25%", top: 4, textAlign: "center", textStyle: { color: MUTED, fontSize: 12 } })),
  series: data.map((items, i) => ({ type: "pie", radius: ["30%", "55%"], data: items, center: [i ? "75%" : "25%", "58%"],
    label: { color: MUTED, fontSize: fs, formatter: fmts[i] }, itemStyle: { borderColor: PANEL, borderWidth: 2 } })),
});
const trend = (data, items) => ({ ...AXIS,
  xAxis: { ...AXIS.xAxis, data: values(data, "k") },
  yAxis: { ...AXIS.yAxis, axisLabel: { color: MUTED, formatter: WAN } }, series: items,
});

const chartRegistry = [
  ["c1", ["dept"], (data) => vBar(data, [series(values(data), "#4f8cff")], rotated)],
  ["c2", ["costM"], (data) => trend(data, [{ type: "line", data: values(data), smooth: true, symbolSize: 5,
    lineStyle: { color: "#3ecf8e", width: 2.5 }, itemStyle: { color: "#3ecf8e" }, areaStyle: { color: "rgba(62,207,142,.12)" } }])],
  ["c3", ["lvl"], (data) => vBar(data, [series(values(data), "#a78bfa")], { interval: 0 })],
  ["c4", ["attr"], (data) => hBar(data, "#ffb454", "{value}%")],
  ["c5", ["leave"], (data) => pie(data, "{b} {c}天")],
  ["c6", ["src", "gen"], (src, gen) => dualPie(["新入职来源", "性别构成"], [pieData(src),
    pieData(gen).map((item, i) => ({ ...item, itemStyle: { color: ["#4f8cff", "#ff6b81"][i] } }))], { fmts: ["{b}", "{b} {d}%"] })],
  ["c7", ["hp"], (data) => vBar(data, [["计划编制", "p", "#3a4a6b"], ["年末实际", "a", "#4f8cff"]]
    .map(([name, key, color]) => series(values(data, key), color, { name, max: 14, br: [4, 4, 0, 0] })), rotated)],
  ["c8", ["costC"], (data) => trend(data, [["基本工资", "b", "#4f8cff"], ["加班费", "o", "#3ecf8e"],
    ["奖金", "bo", "#ffb454"], ["社保(企业)", "ins", "#a78bfa"]].map(([name, key, color]) => ({
    name, type: "line", stack: "cost", data: values(data, key), symbolSize: 0, lineStyle: { width: 0 }, areaStyle: { color } })))],
  ["c9", ["rej"], (data) => pie(data, "{b} {c}单")],
  ["c10", ["costPerHire"], (data) => hBar(data, "#ff6b81", "¥{value}", data.map((row) => ({
    value: row.v == null ? null : Math.round(row.v), label: { show: true, position: "right", color: MUTED, fontSize: 11,
      formatter: row.n ? `入职${row.n}人` : "无入职人数，无法计算" } })))],
  ["c11", ["arc", "real"], (arc, real) => dualPie(["档案离职原因", "面谈真实归因"], [pieData(arc), pieData(real)], { fs: 10 })],
  ["c12", ["goal"], (data) => vBar(data, [series(data.map((row) => Math.round(row.v * 10) / 10), "#3ecf8e", { max: 24 })],
    rotated, { min: 40, axisLabel: { color: MUTED, formatter: "{value}%" } })],
];
const number = (value) => value.toLocaleString("zh-CN");
const kpiRegistry = [
  ["hc", number], ["turnover", String, "%"],
  ["cost", (value) => `¥${(value / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 0 })}`, " 万"],
  ["hire", number], ["funnel", String, "%"],
  ["totalcost", (value) => `¥${Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 1 })}`, " 万"],
  ["plan", String, "%"], ["offer", String, "%"], ["eng", (value) => `${value} / 5`],
];

export async function renderDashboard(client) {
  const charts = [];
  window.addEventListener("resize", () => charts.forEach((chart) => chart.resize()));
  for (const [id, format, unit] of kpiRegistry) {
    const [row] = await client.query(id);
    const element = document.getElementById(`kpi-${id}`);
    element.textContent = format(row.v);
    if (unit) {
      const small = document.createElement("small");
      small.textContent = unit;
      element.append(small);
    }
  }
  for (const [id, queries, options] of chartRegistry) {
    const data = [];
    for (const query of queries) data.push(await client.query(query));
    const element = document.getElementById(id);
    const chart = echarts.init(element, null, { renderer: "canvas" });
    chart.setOption({ ...base, ...options(...data) });
    charts.push(chart);
    element.setAttribute("role", "img");
    element.setAttribute("aria-label", element.closest(".card").querySelector("h3").textContent);
  }
}
