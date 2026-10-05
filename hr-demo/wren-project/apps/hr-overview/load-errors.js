// Register before the blocking CDN script; resource errors require capture.
(() => {
  const show = (message) => {
    window.__loadErrMsg ??= message;
    const status = document.getElementById("status");
    if (status) {
      status.textContent = `加载错误: ${window.__loadErrMsg}`;
      status.className = "err";
    }
    document.querySelectorAll(".loading").forEach((element) => {
      if (element.textContent === "加载中…") element.textContent = "未加载（见顶部错误）";
    });
  };
  window.__showLoadError = show;
  window.addEventListener("error", (event) => {
    const resource = event.target?.src || event.target?.href;
    show(resource ? `资源加载失败: ${resource.split("/").pop()}（本页依赖 cdn.jsdelivr.net / unpkg.com，请检查网络后刷新）`
      : event.message || event.type);
  }, true);
  window.addEventListener("unhandledrejection", (event) => show(event.reason?.message || String(event.reason)));
  document.addEventListener("DOMContentLoaded", () => {
    if (window.__loadErrMsg) show(window.__loadErrMsg);
  });
})();
