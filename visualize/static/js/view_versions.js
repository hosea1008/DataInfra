/**
 * view_versions.js — 版本进度 tab.
 * Env toggle + key checkbox filter (refetch with ?keys= on change) + progress bars.
 */
import { api } from "./api.js";
import { el, replaceChildren, fmtInt, fmtHours, fmtPct, fmtDateTime } from "./util.js";
import {
  segToggle,
  checkboxGroup,
  loadingBlock,
  errorBanner,
  emptyState,
} from "./components.js";

const BUILTIN_LABELS = {
  download_finish: "下载完成",
  register_finish: "注册完成",
};

const state = {
  inited: false,
  env: "production",
  available: [],
  selected: new Set(),
  selectionTouched: false,
};

let rootEl = null;
let filterBox = null;
let contentBox = null;
let toggle = null;
let metaBox = null;
let refreshBtn = null;

function keyLabel(info) {
  if (BUILTIN_LABELS[info.key]) return BUILTIN_LABELS[info.key];
  const extra = [info.step_key, info.version_no != null ? `v${info.version_no}` : null]
    .filter(Boolean)
    .join(" · ");
  return extra ? `${info.key}（${extra}）` : info.key;
}

function keyTitle(info) {
  return [info.step_explain, info.version_note].filter(Boolean).join("；") || info.key;
}

function renderFilter() {
  if (!state.available.length) {
    replaceChildren(filterBox);
    return;
  }
  const group = checkboxGroup(
    state.available.map((a) => ({ value: a.key, label: keyLabel(a), title: keyTitle(a) })),
    state.selected,
    (selected) => {
      state.selected = new Set(selected);
      state.selectionTouched = true;
      load();
    }
  );
  replaceChildren(filterBox, group.element);
}

function renderItems(data) {
  const items = data.items || [];
  if (!items.length) {
    replaceChildren(contentBox, emptyState("当前筛选条件下没有进度项"));
    return;
  }
  const total = data.total_lerobots;
  metaBox.textContent = data.cached_at
    ? `时长统计：精确计算 · 缓存时间：${fmtDateTime(data.cached_at)}；无时长或无版本列时无法计算进度。`
    : "";
  replaceChildren(
    contentBox,
    el(
      "div",
      { class: "progress-list" },
      items.map((item) => {
        const ratio = item.ratio == null ? 0 : Math.max(0, Math.min(1, Number(item.ratio)));
        const sub = [item.step_explain, item.version_note].filter(Boolean).join("；");
        return el(
          "div",
          { class: "progress-item" },
          el(
            "div",
            { class: "progress-item-head" },
            el("span", { class: "progress-item-name", title: item.key }, keyLabel(item)),
            el(
              "span",
              { class: "progress-item-nums" },
              item.progress_kind === "hours"
                ? `${fmtHours(item.completed_hours)} / ${fmtHours(item.total_hours)} 小时 · ${fmtPct(item.ratio)}`
                : `${fmtInt(item.count)} / ${fmtInt(total)} · ${fmtPct(item.ratio)}`
            )
          ),
          sub ? el("div", { class: "progress-item-sub" }, sub) : null,
          el(
            "div",
            {
              class: "progress-track",
              role: "progressbar",
              "aria-valuemin": "0",
              "aria-valuemax": "100",
              "aria-valuenow": String(Math.round(ratio * 100)),
              "aria-label": keyLabel(item),
            },
            el("div", { class: "progress-fill", style: `width: ${(ratio * 100).toFixed(1)}%` })
          )
        );
      })
    )
  );
}

async function load(refresh = false) {
  refreshBtn.disabled = true;
  replaceChildren(contentBox, loadingBlock("加载版本进度…"));
  const keys =
    state.selectionTouched && state.selected.size < state.available.length
      ? [...state.selected]
      : undefined;
  if (state.selectionTouched && state.selected.size === 0) {
    replaceChildren(contentBox, emptyState("请至少勾选一个统计项"));
    refreshBtn.disabled = false;
    return;
  }
  try {
    const data = await api.versionProgress(state.env, keys, {
      refresh,
    });
    // Refresh the checkbox inventory whenever the server sends one.
    if (Array.isArray(data.available)) {
      const prevKeys = state.available.map((a) => a.key).join(",");
      const nextKeys = data.available.map((a) => a.key).join(",");
      if (prevKeys !== nextKeys) {
        state.available = data.available;
        if (!state.selectionTouched) {
          state.selected = new Set(data.available.map((a) => a.key));
        } else {
          const known = new Set(data.available.map((a) => a.key));
          state.selected = new Set([...state.selected].filter((k) => known.has(k)));
        }
        renderFilter();
      }
    }
    renderItems(data);
  } catch (err) {
    if (err.status === 404) {
      replaceChildren(contentBox, emptyState("该环境暂无统计数据，请先执行重置"));
    } else {
      replaceChildren(contentBox, errorBanner(err));
    }
  } finally {
    refreshBtn.disabled = false;
  }
}

export function activate(root) {
  if (state.inited) return;
  state.inited = true;
  rootEl = root;

  toggle = segToggle(
    [
      { value: "production", label: "生产库" },
      { value: "test", label: "测试库" },
    ],
    state.env,
    (env) => {
      state.env = env;
      state.available = [];
      state.selected = new Set();
      state.selectionTouched = false;
      replaceChildren(filterBox);
      load();
    }
  );

  filterBox = el("div", { class: "card" }, el("h3", { class: "card-title" }, "统计项筛选"));
  contentBox = el("div", { class: "card" }, el("h3", { class: "card-title" }, "进度详情"));
  metaBox = el("div", { class: "meta-line" });
  refreshBtn = el("button", { class: "btn", type: "button",
    title: "重新扫描 data 表并更新精确结果，可能需要数分钟", onclick: () => load(true) }, "刷新时长进度");

  replaceChildren(
    rootEl,
    el("div", { class: "card toolbar" }, el("span", { class: "control-label" }, "环境："), toggle.element,
      refreshBtn),
    filterBox,
    metaBox,
    contentBox
  );
  load();
}
