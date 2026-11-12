/**
 * util.js — formatters and DOM helpers shared by all views.
 */

/**
 * Create an element. attrs: class/text/title/value/type/... (setAttribute),
 * on<Event> handlers attach listeners. Children may be nodes or strings.
 */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "dataset" && typeof value === "object") {
      Object.assign(node.dataset, value);
    } else {
      node.setAttribute(key, value);
    }
  }
  appendChildren(node, children);
  return node;
}

function appendChildren(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) {
      appendChildren(node, child);
    } else if (child instanceof Node) {
      node.appendChild(child);
    } else {
      node.appendChild(document.createTextNode(String(child)));
    }
  }
}

/** Replace all children of `parent` with `nodes`. */
export function replaceChildren(parent, ...nodes) {
  parent.textContent = "";
  appendChildren(parent, nodes);
}

/** Integer with zh-CN thousands separators; null/undefined -> "-". */
export function fmtInt(n) {
  if (n === null || n === undefined || Number.isNaN(Number(n))) return "-";
  return Number(n).toLocaleString("zh-CN");
}

/** Hours float, 2 decimals; null -> "-". */
export function fmtHours(h) {
  if (h === null || h === undefined || Number.isNaN(Number(h))) return "-";
  return Number(h).toFixed(2);
}

/** Ratio 0..1 -> "12.3%"; null -> "-". */
export function fmtPct(r) {
  if (r === null || r === undefined || Number.isNaN(Number(r))) return "-";
  return `${(Number(r) * 100).toFixed(1)}%`;
}

/** ISO datetime -> localized string; null -> "-". */
export function fmtDateTime(s) {
  if (!s) return "-";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return String(s);
  return d.toLocaleString("zh-CN", { hour12: false });
}

/** Escape a string for interpolation into HTML (ECharts tooltips render HTML). */
export function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/** Serialize a JSON scalar cell value for display. */
export function scalarToText(v) {
  if (v === null || v === undefined) return null;
  if (typeof v === "string") return v;
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}
