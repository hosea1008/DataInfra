/**
 * view_stats.js — 数据统计 tab (production only).
 * 3 dataset pie cards (lerobot数 / episode数 / 总时长) + column-distribution card.
 */
import { api } from "./api.js";
import {
  el,
  replaceChildren,
  fmtInt,
  fmtHours,
  fmtPct,
  fmtCompact,
  fmtDateTime,
  scalarToText,
  escapeHtml,
} from "./util.js";
import {
  segToggle,
  loadingBlock,
  errorBanner,
  emptyState,
  warningBanner,
} from "./components.js";
import { renderPie } from "./charts.js";

const TOP_K_OPTIONS = [5, 10, 12, 20];

/** Default distribution column (falls back to the first column when absent). */
const DEFAULT_COLUMN = "dataset_type";

const state = {
  inited: false,
  columns: [],
  column: DEFAULT_COLUMN,
  weight: "rows",
  topK: 12,
};

let piesBox = null;
let piesMeta = null;
let distBox = null;
let distMeta = null;
let columnSelect = null;

/* ---------- shared helpers ---------- */

function legendValueOf(unitKind) {
  return (it) =>
    unitKind === "hours" ? `${fmtHours(it.value)}h` : fmtInt(it.value);
}

function sampledBadge(pct) {
  const pctText = pct != null ? ` ${Number(pct).toFixed(1)}%` : "";
  return el(
    "span",
    {
      class: "badge badge-approx",
      title:
        "data 表数据量较大，本结果按抽样统计估算（比例可靠，绝对值为估算）；" +
        "点“刷新”重新抽样，点“全量统计”可精确重算（扫描全表，较慢）",
    },
    `抽样估算${pctText}`
  );
}

/**
 * Disable `buttons` for the duration of `work`, restoring labels afterwards.
 * `work` flips one button's label to a progress text while running.
 */
async function runExclusive(buttons, work) {
  const labels = buttons.map((b) => b.textContent);
  buttons.forEach((b) => {
    b.disabled = true;
  });
  try {
    await work();
  } finally {
    buttons.forEach((b, i) => {
      b.disabled = false;
      b.textContent = labels[i];
    });
  }
}

/* ---------- dataset pies ---------- */

function pieTooltip(unitKind) {
  return (p) => {
    const name = escapeHtml(p.name);
    const value =
      unitKind === "hours" ? `${fmtHours(p.value)} 小时` : fmtInt(p.value);
    // p.percent is recomputed over visible slices only; prefer the original
    // full-dataset ratio from meta so tooltip stays consistent with legend.
    const meta = p.data && p.data.meta;
    const ratio =
      meta && meta.ratio != null
        ? fmtPct(meta.ratio)
        : p.percent != null
          ? `${p.percent.toFixed(1)}%`
          : "-";
    return `${name}<br/>数值：${value}<br/>占比：${ratio}`;
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

async function loadPies(mode = null) {
  // "sample"/"exact" keep the current pies visible while recomputing.
  if (!mode || !piesBox.firstChild) {
    replaceChildren(
      piesBox,
      loadingBlock(
        mode === "exact"
          ? "正在全量精确统计（扫描 data 全表，可能需要数分钟）…"
          : mode === "sample"
            ? "正在重新抽样统计…"
            : "加载数据集统计…"
      )
    );
  }
  try {
    const data = await api.statsDatasets(mode != null, mode === "exact");
    const specs = [
      {
        title: "lerobot 数",
        items: data.lerobots,
        unit: "count",
        center: { value: fmtCompact(data.totals.lerobots), label: "lerobot 总数" },
      },
      {
        title: "episode 数",
        items: data.episodes,
        unit: "count",
        center: { value: fmtCompact(data.totals.episodes), label: "episode 总数" },
      },
      {
        title: "总时长（小时）",
        items: data.hours,
        unit: "hours",
        center: { value: fmtCompact(data.totals.hours), label: "总小时" },
      },
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
        legendValue: legendValueOf(spec.unit),
        centerLabel: spec.center,
      });
    }
    const metaChildren = [
      el("span", {
        class: "cached-at",
        text: `缓存时间：${fmtDateTime(data.cached_at)}`,
      }),
    ];
    if (data.sampled) metaChildren.push(sampledBadge(data.sample_pct));
    replaceChildren(piesMeta, ...metaChildren);
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

function renderDistMeta(data, warningText) {
  const children = [];
  if (warningText) children.push(warningBanner(warningText));
  const approx = data.sampled ? "≈ " : "";
  children.push(
    el("span", {
      class: "cached-at",
      text: `总行数 ${approx}${fmtInt(data.total_rows)} · 总时长 ${approx}${fmtHours(data.total_hours)} 小时`,
    })
  );
  if (data.sampled) children.push(sampledBadge(data.sample_pct));
  children.push(
    el("span", {
      class: "cached-at",
      text: `缓存时间：${fmtDateTime(data.cached_at)}`,
    })
  );
  replaceChildren(distMeta, ...children);
}

async function loadDistribution(warningText = null) {
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
        legendValue: legendValueOf(useHours ? "hours" : "count"),
        centerLabel: useHours
          ? { value: fmtCompact(data.total_hours), label: "总小时" }
          : { value: fmtCompact(data.total_rows), label: "总行数" },
      });
    }
    renderDistMeta(data, warningText);
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
      state.column = state.columns.some((c) => c.name === DEFAULT_COLUMN)
        ? DEFAULT_COLUMN
        : state.columns[0].name;
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
  piesMeta = el("div", { class: "meta-line" });
  distBox = el("div");
  distMeta = el("div", { class: "meta-line" });

  columnSelect = el("select", { "aria-label": "统计列" });

  const piesSampleBtn = el(
    "button",
    {
      class: "btn",
      type: "button",
      title: "重新抽样估算并更新缓存（较快；表较小时自动退化为精确统计）",
      onclick: () =>
        runExclusive([piesSampleBtn, piesExactBtn], async () => {
          piesSampleBtn.textContent = "抽样中…";
          await loadPies("sample");
        }),
    },
    "刷新"
  );

  const piesExactBtn = el(
    "button",
    {
      class: "btn",
      type: "button",
      title: "全量精确统计并更新缓存（需扫描 data 全表，可能需要数分钟）",
      onclick: () =>
        runExclusive([piesSampleBtn, piesExactBtn], async () => {
          piesExactBtn.textContent = "全量统计中…";
          await loadPies("exact");
        }),
    },
    "全量统计"
  );

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

  const distSampleBtn = el(
    "button",
    {
      class: "btn",
      type: "button",
      title: "重新抽样估算本列并更新缓存（较快；表较小时自动退化为精确统计）",
      onclick: () =>
        runExclusive([distSampleBtn, distExactBtn], async () => {
          distSampleBtn.textContent = "抽样中…";
          let err = null;
          try {
            await api.columnDistribution({
              column: state.column,
              weight: state.weight,
              topK: state.topK,
              refresh: true,
            });
          } catch (e) {
            err = e;
          }
          loadDistribution(
            err ? `重新抽样失败：${err.message}（以下为缓存结果）` : null
          );
        }),
    },
    "刷新"
  );

  const distExactBtn = el(
    "button",
    {
      class: "btn",
      type: "button",
      title: "全量精确统计本列并更新缓存（需扫描 data 全表，可能需要数分钟）",
      onclick: () =>
        runExclusive([distSampleBtn, distExactBtn], async () => {
          distExactBtn.textContent = "全量统计中…";
          let err = null;
          try {
            await api.columnDistribution({
              column: state.column,
              weight: state.weight,
              topK: state.topK,
              refresh: true,
              exact: true,
            });
          } catch (e) {
            err = e;
          }
          loadDistribution(
            err ? `全量统计失败：${err.message}（以下为缓存结果）` : null
          );
        }),
    },
    "全量统计"
  );

  replaceChildren(
    root,
    el(
      "div",
      { class: "card" },
      el(
        "div",
        { class: "stats-card-head" },
        el("h3", { class: "card-title" }, "数据集统计（生产库）"),
        el(
          "div",
          { class: "btn-group" },
          piesSampleBtn,
          piesExactBtn
        )
      ),
      piesMeta,
      piesBox
    ),
    el(
      "div",
      { class: "card" },
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
        distSampleBtn,
        distExactBtn
      ),
      distBox,
      distMeta
    )
  );

  loadPies();
  loadColumnsAndDist();
}
