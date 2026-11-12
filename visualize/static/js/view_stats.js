/**
 * view_stats.js — 数据统计 tab (production only).
 * 3 dataset pie cards (lerobot数 / episode数 / 总时长) + column-distribution card.
 */
import { api } from "./api.js";
import { el, replaceChildren, fmtInt, fmtHours, fmtPct, fmtDateTime, scalarToText, escapeHtml } from "./util.js";
import {
  segToggle,
  loadingBlock,
  errorBanner,
  emptyState,
} from "./components.js";
import { renderPie } from "./charts.js";

const TOP_K_OPTIONS = [5, 10, 12, 20];

const state = {
  inited: false,
  columns: [],
  column: "",
  weight: "rows",
  topK: 12,
};

let piesBox = null;
let piesMeta = null;
let distBox = null;
let distMeta = null;
let columnSelect = null;

/* ---------- dataset pies ---------- */

function pieTooltip(unitKind) {
  return (p) => {
    const name = escapeHtml(p.name);
    const value =
      unitKind === "hours"
        ? `${fmtHours(p.value)} 小时`
        : `${fmtInt(p.value)}`;
    const ratio = p.percent != null ? `${Number(p.percent).toFixed(1)}%` : "-";
    return `${name}<br/>数值：${value}<br/>占比：${ratio}`;
  };
}

function legendText(unitKind) {
  return (it) => {
    const value = unitKind === "hours" ? `${fmtHours(it.value)}h` : fmtInt(it.value);
    const pct = it.ratio != null ? `${(it.ratio * 100).toFixed(1)}%` : "-";
    return `${it.name} ${value} (${pct})`;
  };
}

function toPieData(items) {
  return (items || []).map((it) => ({
    name: it.dataset_name == null ? "NULL" : String(it.dataset_name),
    value: it.value == null ? 0 : Number(it.value),
    ratio: it.ratio,
    meta: it,
  }));
}

async function loadPies() {
  replaceChildren(piesBox, loadingBlock("加载数据集统计…"));
  try {
    const data = await api.statsDatasets(false);
    const specs = [
      { title: "lerobot 数", items: data.lerobots, unit: "count" },
      { title: "episode 数", items: data.episodes, unit: "count" },
      { title: "总时长（小时）", items: data.hours, unit: "hours" },
    ];
    const cards = specs.map((spec) => {
      const canvas = el("div", { class: "pie-canvas" });
      const card = el(
        "div",
        { class: "pie-card" },
        el("h3", {}, spec.title),
        canvas
      );
      return { spec, canvas, card };
    });
    replaceChildren(piesBox, cards.map((c) => c.card));
    for (const { spec, canvas } of cards) {
      const pieData = toPieData(spec.items);
      if (!pieData.length) {
        replaceChildren(canvas, emptyState("暂无数据"));
        continue;
      }
      renderPie(canvas, {
        data: pieData,
        tooltipFormatter: pieTooltip(spec.unit),
        legendFormatter: legendText(spec.unit),
      });
    }
    replaceChildren(
      piesMeta,
      el("span", { class: "cached-at", text: `缓存时间：${fmtDateTime(data.cached_at)}` })
    );
  } catch (err) {
    replaceChildren(piesBox, errorBanner(err));
  }
}

/* ---------- column distribution ---------- */

function distDisplayValue(v) {
  if (v === "__other__") return "其他";
  const text = scalarToText(v);
  return text === null ? "NULL" : text;
}

function distTooltip(p) {
  const meta = p.data && p.data.meta;
  if (!meta) return escapeHtml(p.name);
  return [
    `取值：${escapeHtml(p.name)}`,
    `行数：${fmtInt(meta.rows)}`,
    `时长：${fmtHours(meta.hours)} 小时`,
    `行数占比：${fmtPct(meta.ratio_rows)}`,
    `时长占比：${fmtPct(meta.ratio_hours)}`,
  ].join("<br/>");
}

async function loadDistribution() {
  if (!state.column) return;
  replaceChildren(distBox, loadingBlock("加载列分布…"));
  try {
    const data = await api.columnDistribution({
      column: state.column,
      weight: state.weight,
      topK: state.topK,
      refresh: false,
    });
    const useHours = state.weight === "hours";
    const items = (data.items || []).map((it) => ({
      name: distDisplayValue(it.value),
      value: Number((useHours ? it.hours : it.rows) || 0),
      ratio: useHours ? it.ratio_hours : it.ratio_rows,
      meta: { ...it, value: it.value },
    }));
    if (data.other) {
      items.push({
        name: "其他",
        value: Number((useHours ? data.other.hours : data.other.rows) || 0),
        ratio: useHours ? data.other.ratio_hours : data.other.ratio_rows,
        meta: { ...data.other, value: "__other__" },
      });
    }
    if (!items.length) {
      replaceChildren(distBox, emptyState("该列暂无分布数据"));
    } else {
      const canvas = el("div", { class: "dist-canvas" });
      replaceChildren(distBox, canvas);
      renderPie(canvas, {
        data: items,
        tooltipFormatter: distTooltip,
        legendFormatter: legendText(useHours ? "hours" : "count"),
      });
    }
    replaceChildren(
      distMeta,
      el("span", {
        class: "cached-at",
        text: `总行数 ${fmtInt(data.total_rows)} · 总时长 ${fmtHours(data.total_hours)} 小时 · 缓存时间：${fmtDateTime(data.cached_at)}`,
      })
    );
  } catch (err) {
    replaceChildren(distBox, errorBanner(err));
  }
}

function buildColumnSelect() {
  const groups = { static: [], dynamic: [], other: [] };
  for (const col of state.columns) {
    if (col.kind === "static") groups.static.push(col);
    else if (col.kind === "dynamic") groups.dynamic.push(col);
    else groups.other.push(col);
  }
  const groupSpecs = [
    ["静态列", groups.static],
    ["动态列", groups.dynamic],
    ["其他", groups.other],
  ].filter(([, cols]) => cols.length);

  const select = el("select", {
    "aria-label": "统计列",
    onchange: () => {
      state.column = select.value;
      loadDistribution();
    },
  });
  for (const [label, cols] of groupSpecs) {
    const og = el("optgroup", { label });
    for (const col of cols) {
      const opt = el(
        "option",
        { value: col.name, title: col.note || col.name },
        col.note ? `${col.name}（${col.note}）` : col.name
      );
      og.appendChild(opt);
    }
    select.appendChild(og);
  }
  return select;
}

async function loadColumnsAndDist() {
  replaceChildren(distBox, loadingBlock("加载列信息…"));
  try {
    const data = await api.statsColumns();
    state.columns = data.columns || [];
    if (!state.columns.length) {
      replaceChildren(distBox, emptyState("没有可统计的列"));
      return;
    }
    if (!state.columns.some((c) => c.name === state.column)) {
      state.column = state.columns[0].name;
    }
    const fresh = buildColumnSelect();
    fresh.value = state.column;
    columnSelect.replaceWith(fresh);
    columnSelect = fresh;
    await loadDistribution();
  } catch (err) {
    replaceChildren(distBox, errorBanner(err));
  }
}

export function activate(root) {
  if (state.inited) return;
  state.inited = true;

  piesBox = el("div", { class: "pie-row" });
  piesMeta = el("div");
  distBox = el("div");
  distMeta = el("div", { style: "margin-top: 8px" });

  columnSelect = el("select", { "aria-label": "统计列" });

  const weightToggle = segToggle(
    [
      { value: "rows", label: "按行数" },
      { value: "hours", label: "按时长" },
    ],
    state.weight,
    (w) => {
      state.weight = w;
      loadDistribution();
    }
  );

  const topKSelect = el(
    "select",
    {
      "aria-label": "Top K",
      onchange: () => {
        state.topK = Number(topKSelect.value);
        loadDistribution();
      },
    },
    TOP_K_OPTIONS.map((n) => {
      const opt = el("option", { value: String(n) }, `Top ${n}`);
      if (n === state.topK) opt.selected = true;
      return opt;
    })
  );

  const refreshBtn = el(
    "button",
    {
      class: "btn",
      type: "button",
      onclick: async () => {
        refreshBtn.disabled = true;
        try {
          await api.columnDistribution({
            column: state.column,
            weight: state.weight,
            topK: state.topK,
            refresh: true,
          });
        } catch {
          /* refresh failure surfaces via the reload below if still broken */
        }
        refreshBtn.disabled = false;
        loadDistribution();
      },
    },
    "刷新"
  );

  replaceChildren(
    root,
    el("div", { class: "card" },
      el("h3", { class: "card-title" }, "数据集统计（生产库）"),
      piesMeta,
      piesBox
    ),
    el("div", { class: "card" },
      el("h3", { class: "card-title" }, "列值分布"),
      el(
        "div",
        { class: "dist-controls" },
        el("span", { class: "control-label" }, "统计列："),
        columnSelect,
        el("span", { class: "control-label" }, "权重："),
        weightToggle.element,
        el("span", { class: "control-label" }, "Top K："),
        topKSelect,
        refreshBtn
      ),
      distBox,
      distMeta
    )
  );

  loadPies();
  loadColumnsAndDist();
}
