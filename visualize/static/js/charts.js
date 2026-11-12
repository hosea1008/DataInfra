/**
 * charts.js — ECharts pie factory with an HTML side-legend.
 *
 * Layout inside `dom` (a .pie-canvas/.dist-canvas sized div):
 *   [ chart area (ECharts donut) | scrollable HTML legend ]
 * The legend never overlaps the chart (flex layout), and each row is
 * name-...-left / value+pct-...-right justified with tabular numerals.
 * Clicking a row toggles the slice on/off (colors stay stable per name).
 *
 * Requires window.echarts (loaded from /vendor/echarts.min.js before modules run).
 */
import { el, replaceChildren, fmtInt } from "./util.js";

/** Fixed palette cycled per data index so slice colors survive toggles. */
const PALETTE = [
  "#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed",
  "#0891b2", "#db2777", "#65a30d", "#ea580c", "#0d9488",
  "#6366f1", "#b45309", "#059669", "#9333ea",
];

const instances = new Map();

const containerObserver = new ResizeObserver((entries) => {
  for (const entry of entries) {
    const chart = instances.get(entry.target);
    if (chart) chart.resize();
  }
});

window.addEventListener("resize", () => {
  for (const chart of instances.values()) chart.resize();
});

function legendRow(item, color, onToggle) {
  const valueText =
    item.valueText != null ? item.valueText : fmtInt(item.value);
  const pctText =
    item.ratio != null ? `${(item.ratio * 100).toFixed(1)}%` : "-";

  const dot = el("span", {
    class: "pie-legend-dot",
    style: `background:${color}`,
  });
  const row = el(
    "div",
    {
      class: "pie-legend-row",
      role: "button",
      tabindex: 0,
      title: `${item.name}（点击隐藏/显示）`,
      onclick: () => onToggle(),
      onkeydown: (evt) => {
        if (evt.key === "Enter" || evt.key === " ") {
          evt.preventDefault();
          onToggle();
        }
      },
    },
    dot,
    el("span", { class: "pie-legend-name", text: item.name }),
    el("span", { class: "pie-legend-num", text: valueText }),
    el("span", { class: "pie-legend-pct", text: pctText })
  );
  return { row, dot };
}

/**
 * Render (or re-render) a donut chart with an HTML legend into `dom`.
 * Any previous chart bound to `dom` is disposed first.
 *
 * data: [{name, value, ratio?, meta?}] — meta is passed through to the
 *   tooltip formatter; valueText (optional) overrides the legend value cell.
 * tooltipFormatter(params) -> HTML string.
 * legendValue(item) -> string shown in the legend value cell (default fmtInt).
 * centerLabel: optional {value, label} drawn inside the donut hole (e.g. total).
 */
export function renderPie(
  dom,
  { data, tooltipFormatter, legendValue, centerLabel }
) {
  if (!window.echarts) {
    throw new Error("ECharts 未加载");
  }
  const prev = instances.get(dom);
  if (prev) {
    prev.dispose();
    instances.delete(dom);
    containerObserver.unobserve(dom);
  }

  const chartArea = el("div", { class: "pie-chart-area" });
  const legendBox = el("div", { class: "pie-legend" });
  dom.classList.add("pie-flex");
  replaceChildren(dom, chartArea, legendBox);

  const chart = window.echarts.init(chartArea);
  instances.set(dom, chart);
  containerObserver.observe(dom);

  const colorOf = (idx) => PALETTE[idx % PALETTE.length];
  const hidden = new Set();

  function applyData() {
    chart.setOption({
      series: [
        {
          data: data
            .map((item, idx) => ({
              name: item.name,
              value: item.value,
              meta: item.meta,
              itemStyle: { color: colorOf(idx) },
            }))
            .filter((item) => !hidden.has(item.name)),
        },
      ],
    });
  }

  data.forEach((item, idx) => {
    const color = colorOf(idx);
    const { row, dot } = legendRow(
      legendValue ? { ...item, valueText: legendValue(item) } : item,
      color,
      () => {
        if (hidden.has(item.name)) {
          hidden.delete(item.name);
          row.classList.remove("is-off");
          dot.style.background = color;
        } else {
          hidden.add(item.name);
          row.classList.add("is-off");
          dot.style.background = "var(--gray-dot)";
        }
        applyData();
      }
    );
    legendBox.appendChild(row);
  });

  chart.setOption({
    tooltip: {
      trigger: "item",
      confine: true,
      formatter: tooltipFormatter,
    },
    title: centerLabel
      ? {
          text: centerLabel.value,
          subtext: centerLabel.label,
          left: "center",
          top: "middle",
          itemGap: 2,
          textStyle: { fontSize: 18, fontWeight: 600, color: "#1f2329" },
          subtextStyle: { fontSize: 11, color: "#5c6370" },
        }
      : undefined,
    series: [
      {
        type: "pie",
        radius: ["58%", "82%"],
        center: ["50%", "50%"],
        itemStyle: { borderColor: "#fff", borderWidth: 1 },
        label: { show: false },
        labelLine: { show: false },
        data: [],
      },
    ],
  });
  applyData();
  return chart;
}

/** Dispose every live chart (e.g. when leaving a tab). */
export function disposeAll() {
  for (const chart of instances.values()) chart.dispose();
  instances.clear();
  containerObserver.disconnect();
}
