/**
 * components.js — reusable DOM builders: loading spinner, error banner,
 * empty state, checkbox group, pager, data grid.
 */
import { el, fmtInt, scalarToText } from "./util.js";

export function spinner(small = false) {
  return el("span", {
    class: small ? "spinner spinner-sm" : "spinner",
    role: "status",
    "aria-label": "加载中",
  });
}

export function loadingBlock(text = "加载中…") {
  return el("div", { class: "loading-block" }, spinner(), el("span", { text }));
}

export function errorBanner(err) {
  const msg = err && err.message ? err.message : String(err);
  return el("div", { class: "banner banner-error", role: "alert" }, `加载失败：${msg}`);
}

export function warningBanner(text) {
  return el("div", { class: "banner banner-warning", role: "alert" }, text);
}

export function emptyState(text) {
  return el("div", { class: "empty-state" }, text);
}

/**
 * Segmented toggle. options: [{value, label}]. onChange(value).
 * Returns { element, setActive }.
 */
export function segToggle(options, active, onChange) {
  const buttons = new Map();
  const element = el(
    "div",
    { class: "seg-toggle", role: "tablist" },
    options.map((opt) => {
      const btn = el(
        "button",
        {
          type: "button",
          class: opt.value === active ? "is-active" : "",
          onclick: () => {
            setActive(opt.value);
            onChange(opt.value);
          },
        },
        opt.label
      );
      buttons.set(opt.value, btn);
      return btn;
    })
  );
  function setActive(value) {
    for (const [v, btn] of buttons) {
      btn.classList.toggle("is-active", v === value);
    }
  }
  return { element, setActive };
}

/**
 * Checkbox group. items: [{value, label, title?}].
 * onChange(selectedValues: string[]).
 */
export function checkboxGroup(items, selectedSet, onChange) {
  const boxes = new Map();
  const element = el(
    "div",
    { class: "checkbox-group" },
    items.map((item) => {
      const input = el("input", {
        type: "checkbox",
        value: item.value,
        onchange: () => {
          const selected = [...boxes.values()]
            .filter((b) => b.checked)
            .map((b) => b.value);
          onChange(selected);
        },
      });
      input.checked = selectedSet.has(item.value);
      boxes.set(item.value, input);
      return el(
        "label",
        { class: "checkbox-item", title: item.title || item.label },
        input,
        el("span", { text: item.label })
      );
    })
  );
  return { element, boxes };
}

/**
 * Prev/next pager. onPage(newOffset).
 * Returns { element, update }.
 */
export function pager({ limit, offset, estRows, onPage }) {
  const info = el("span");
  const prevBtn = el("button", { class: "btn", type: "button", onclick: () => onPage(offset - limit) }, "上一页");
  const nextBtn = el("button", { class: "btn", type: "button", onclick: () => onPage(offset + limit) }, "下一页");

  function update({ limit: l, offset: o, estRows: total }) {
    limit = l;
    offset = o;
    estRows = total;
    const from = o + 1;
    const last = total === null || total === undefined ? o + l : Math.min(o + l, total);
    const to = Math.max(from, last);
    const totalText = total === null || total === undefined ? "总数未知" : `共约 ${fmtInt(total)} 行`;
    info.textContent = `第 ${fmtInt(from)}–${fmtInt(to)} 行 · ${totalText}`;
    prevBtn.disabled = o <= 0;
    nextBtn.disabled = total !== null && total !== undefined ? o + l >= total : false;
  }

  update({ limit, offset, estRows });
  const element = el("div", { class: "pager" }, prevBtn, nextBtn, info);
  return { element, update };
}

/**
 * Preview data grid.
 * columns: string[]; rows: [[{v, t}]].
 * null -> gray "NULL"; t=true -> truncation badge + full (server-truncated) value in title.
 */
export function dataGrid(columns, rows) {
  const thead = el(
    "thead",
    {},
    el(
      "tr",
      {},
      columns.map((c) => el("th", { title: c }, c))
    )
  );
  const tbody = el(
    "tbody",
    {},
    rows.map((row) =>
      el(
        "tr",
        {},
        row.map((cell) => {
          const text = scalarToText(cell && cell.v);
          if (text === null) {
            return el("td", {}, el("span", { class: "cell-null", text: "NULL", title: "NULL" }));
          }
          const truncated = Boolean(cell && cell.t);
          const content = [el("span", { class: "cell-text", text })];
          if (truncated) {
            content.push(
              el("span", { class: "trunc-badge", title: "内容已被服务端截断", "aria-label": "内容已截断" }, "…")
            );
          }
          return el(
            "td",
            {},
            el("div", { class: "cell-box", title: truncated ? `${text}（已截断）` : text }, content)
          );
        })
      )
    )
  );
  return el("div", { class: "grid-wrap" }, el("table", { class: "data-grid" }, thead, tbody));
}
