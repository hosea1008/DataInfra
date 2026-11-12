/** Dataset storage inventory, isolated from existing charts and controls. */
import { api } from "./api.js";
import { el, replaceChildren, fmtDateTime } from "./util.js";
import { loadingBlock, errorBanner, emptyState } from "./components.js";

/** Keep the complete path visible, with natural line breaks after separators. */
function pathText(value) {
  const span = el("span", { class: "dataset-path-text", title: value });
  const parts = String(value).split("/");
  parts.forEach((part, index) => {
    if (index) span.append("/", el("wbr"));
    span.append(part);
  });
  return span;
}

function pathBlock(label, path) {
  return el("div", { class: "dataset-path-block" },
    el("span", { class: "dataset-path-label" }, label),
    pathText(path));
}

function datasetPathsCard() {
  const body = el("div");
  const meta = el("div", { class: "meta-line" });
  const search = el("input", { type: "search", placeholder: "筛选数据集或路径", "aria-label": "筛选数据集路径" });
  let items = [];
  function render() {
    const query = search.value.trim().toLowerCase();
    const filtered = items.filter((item) => JSON.stringify(item).toLowerCase().includes(query));
    if (!filtered.length) {
      replaceChildren(body, emptyState("没有匹配的数据集路径"));
      return;
    }
    const rows = filtered.map((item) => {
      const chain = el("div", { class: "dataset-path-chain" });
      for (const [index, link] of item.links.entries()) {
        chain.appendChild(el("div", { class: "dataset-path-link" },
          el("span", { class: "dataset-path-label" }, `第 ${index + 1} 层软链接`),
          pathBlock("链接", link.path),
          pathBlock("指向", link.target)));
      }
      chain.appendChild(item.resolved_path
        ? pathBlock("真实目录", item.resolved_path)
        : el("span", { class: "dataset-path-unresolved" }, "真实目录无法确认"));
      return el("tr", {},
        el("td", {}, item.dataset_name ?? "NULL"),
        el("td", {}, String(item.sample_count)),
        el("td", {}, item.dataset_path ? pathBlock("数据库路径", item.dataset_path) : "—"),
        el("td", {}, chain),
        el("td", {}, item.warnings.length ? item.warnings.join("；") : (item.links.length ? "已解析软链接" : "真实目录")));
    });
    replaceChildren(body, el("div", { class: "dataset-path-scroll" }, el("table", { class: "kv-table dataset-path-table" },
      el("thead", {}, el("tr", {}, ["数据集", "采样数", "推断存储目录", "软链接各层 → 真实目录", "核实结果"].map((t) => el("th", {}, t)))),
      el("tbody", {}, rows))));
  }
  async function load(refresh = false) {
    button.disabled = true;
    replaceChildren(body, loadingBlock("正在采样并解析数据集路径…"));
    try {
      const data = await api.datasetPaths(refresh);
      items = data.items;
      meta.textContent = `缓存时间：${fmtDateTime(data.cached_at)} · 每个数据集最多随机采样 ${data.sample_size} 条`;
      render();
    } catch (err) {
      replaceChildren(body, errorBanner(err));
    } finally {
      button.disabled = false;
    }
  }
  const button = el("button", { class: "btn", type: "button", onclick: () => load(true) }, "重新采样并解析");
  search.addEventListener("input", render);
  const card = el("div", { class: "card" },
    el("div", { class: "stats-card-head" }, el("h3", { class: "card-title" }, "数据集存储路径（生产库）"), button),
    el("p", { class: "meta-line" }, "按采样 lerobot 的父目录推断公共目录，结果不代表全部存储位置。软链接按网站服务所在机器的文件系统逐层核实；请确保服务可访问相同挂载路径。"),
    search, meta, body);
  load();
  return card;
}

export function activate(root) {
  if (root.firstChild) return;
  replaceChildren(root, datasetPathsCard());
}
