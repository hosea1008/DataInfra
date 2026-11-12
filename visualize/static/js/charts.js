/**
 * charts.js — ECharts pie factory with instance lifecycle (dispose on re-render).
 * Requires window.echarts (loaded from /vendor/echarts.min.js before modules run).
 */

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

/**
 * Render (or re-render) a pie chart into `dom`.
 * Any previous chart bound to `dom` is disposed first.
 *
 * data: [{name, value, ratio?, meta?}]  — meta is passed through to the tooltip formatter.
 * tooltipFormatter(params) -> HTML string.
 * legendFormatter(item) -> string, optional; item is the data entry for the
 * legend name. Defaults to "name (pct%)".
 */
export function renderPie(dom, { data, tooltipFormatter, legendFormatter, legendType = "scroll" }) {
  if (!window.echarts) {
    throw new Error("ECharts 未加载");
  }
  const prev = instances.get(dom);
  if (prev) {
    prev.dispose();
    instances.delete(dom);
    containerObserver.unobserve(dom);
  }
  const chart = window.echarts.init(dom);
  instances.set(dom, chart);
  containerObserver.observe(dom);
  chart.setOption({
    tooltip: {
      trigger: "item",
      confine: true,
      formatter: tooltipFormatter,
    },
    legend: {
      type: legendType,
      orient: "vertical",
      right: 0,
      top: "middle",
      formatter: (name) => {
        const item = data.find((d) => d.name === name);
        if (!item) return name;
        if (legendFormatter) return legendFormatter(item);
        const pct = item.ratio != null ? ` ${(item.ratio * 100).toFixed(1)}%` : "";
        return `${item.name}${pct}`;
      },
      textStyle: { fontSize: 12 },
    },
    series: [
      {
        type: "pie",
        radius: ["38%", "68%"],
        center: ["32%", "50%"],
        avoidLabelOverlap: true,
        labelLayout: { hideOverlap: true },
        itemStyle: { borderColor: "#fff", borderWidth: 1 },
        label: {
          formatter: (p) => `${p.name} ${p.percent.toFixed(1)}%`,
          fontSize: 12,
        },
        labelLine: { length: 8, length2: 6 },
        data: data.map(({ name, value, meta }) => ({ name, value, meta })),
      },
    ],
  });
  return chart;
}

/** Dispose every live chart (e.g. when leaving a tab). */
export function disposeAll() {
  for (const chart of instances.values()) chart.dispose();
  instances.clear();
  containerObserver.disconnect();
}
