/**
 * view_reset.js — 重置测试环境 tab: 3-step wizard.
 * Step1 选择数据集 -> Step2 生成复制计划 -> Step3 确认执行.
 */
import { api } from "./api.js";
import { el, replaceChildren, fmtInt, fmtHours } from "./util.js";
import {
  loadingBlock,
  errorBanner,
  warningBanner,
  emptyState,
  spinner,
} from "./components.js";

const META_LABELS = {
  step_key: "step_key 表",
  version_info: "version_info 表",
  column_info: "column_info 表",
};

const COPIED_LABELS = {
  step_key: "step_key",
  version_info: "version_info",
  column_info: "column_info",
  status: "status",
  lerobot_episode: "lerobot_episode",
  data: "data",
};

const state = {
  inited: false,
  step: 1,
  datasets: [],
  defaultN: 10,
  selected: new Set(),
  n: 10,
  plan: null,
  executing: false,
  result: null,
};

let stepBox = null;
let indicatorBox = null;

/* ---------- step indicator ---------- */

const STEP_TITLES = ["选择数据集", "复制计划", "确认执行"];

function renderIndicator() {
  const nodes = [];
  STEP_TITLES.forEach((title, i) => {
    const num = i + 1;
    const cls =
      num === state.step ? "step-node is-active" : num < state.step ? "step-node is-done" : "step-node";
    nodes.push(
      el("div", { class: cls }, el("span", { class: "step-num", text: String(num) }), el("span", { text: title }))
    );
    if (num < STEP_TITLES.length) nodes.push(el("div", { class: "step-line" }));
  });
  replaceChildren(indicatorBox, nodes);
}

/* ---------- step 1: datasets ---------- */

async function loadDatasets() {
  replaceChildren(stepBox, loadingBlock("加载数据集列表…"));
  try {
    const data = await api.resetDatasets();
    state.datasets = data.datasets || [];
    state.defaultN = data.default_n ?? 10;
    state.n = state.defaultN;
    state.selected = new Set();
    renderStep1();
  } catch (err) {
    replaceChildren(stepBox, errorBanner(err));
  }
}

function renderStep1() {
  if (!state.datasets.length) {
    replaceChildren(stepBox, emptyState("生产库中没有可用数据集"));
    return;
  }
  const boxes = new Map();
  const list = el(
    "div",
    { class: "dataset-list" },
    state.datasets.map((d) => {
      const input = el("input", {
        type: "checkbox",
        value: d.dataset_name,
        onchange: () => {
          if (input.checked) state.selected.add(d.dataset_name);
          else state.selected.delete(d.dataset_name);
        },
      });
      input.checked = state.selected.has(d.dataset_name);
      boxes.set(d.dataset_name, input);
      return el(
        "label",
        { class: "checkbox-item" },
        input,
        el("span", { text: d.dataset_name }),
        el("span", { class: "badge", text: `${fmtInt(d.lerobot_count)} lerobot` })
      );
    })
  );

  const nextBtn = el(
    "button",
    {
      class: "btn btn-primary",
      type: "button",
      onclick: () => {
        if (!state.selected.size) return;
        state.step = 2;
        state.plan = null;
        renderIndicator();
        renderStep2();
      },
    },
    "下一步：生成复制计划"
  );

  const counter = el("span", { class: "control-label" });

  const updateSelectionUI = () => {
    nextBtn.disabled = state.selected.size === 0;
    counter.textContent = `已选 ${state.selected.size} / ${state.datasets.length} 个数据集`;
  };
  list.addEventListener("change", updateSelectionUI);
  updateSelectionUI();

  replaceChildren(
    stepBox,
    el("div", { class: "toolbar" },
      el("button", {
        class: "btn btn-link", type: "button",
        onclick: () => {
          state.selected = new Set(state.datasets.map((d) => d.dataset_name));
          for (const b of boxes.values()) b.checked = true;
          updateSelectionUI();
        },
      }, "全选"),
      el("button", {
        class: "btn btn-link", type: "button",
        onclick: () => {
          state.selected.clear();
          for (const b of boxes.values()) b.checked = false;
          updateSelectionUI();
        },
      }, "清空"),
      el("span", { class: "spacer" }),
      counter
    ),
    list,
    el("div", { class: "wizard-actions" }, nextBtn)
  );
}

/* ---------- step 2: plan ---------- */

function renderStep2() {
  const nInput = el("input", {
    type: "number",
    min: "1",
    max: "500",
    inputmode: "numeric",
    value: String(state.n),
    "aria-label": "随机采样 lerobot 总数 N",
    onchange: () => {
      const v = Math.max(1, Math.min(500, Number(nInput.value) || state.defaultN));
      nInput.value = String(v);
      state.n = v;
    },
  });

  const planBox = el("div", { style: "margin-top: 16px" });
  const genBtn = el("button", { class: "btn btn-primary", type: "button" }, "生成复制计划");

  genBtn.addEventListener("click", async () => {
    genBtn.disabled = true;
    replaceChildren(planBox, loadingBlock("正在生成复制计划…"));
    try {
      const plan = await api.resetPlan([...state.selected], state.n);
      state.plan = plan;
      renderPlan(planBox, plan);
    } catch (err) {
      state.plan = null;
      replaceChildren(planBox, errorBanner(err));
    } finally {
      genBtn.disabled = false;
    }
  });

  const nextBtn = el(
    "button",
    {
      class: "btn btn-primary",
      type: "button",
      onclick: () => {
        if (!state.plan) return;
        state.step = 3;
        renderIndicator();
        renderStep3();
      },
    },
    "下一步：确认执行"
  );
  nextBtn.disabled = !state.plan;
  // Re-evaluate after each successful plan render.
  const observer = new MutationObserver(() => {
    nextBtn.disabled = !state.plan;
  });
  observer.observe(planBox, { childList: true });

  replaceChildren(
    stepBox,
    el("div", { class: "toolbar" },
      el("span", { class: "control-label" }, "随机采样总数 N："),
      nInput,
      genBtn,
      el("span", { class: "spacer" }),
      el("button", {
        class: "btn", type: "button",
        onclick: () => {
          state.step = 1;
          renderIndicator();
          renderStep1();
        },
      }, "上一步"),
      nextBtn
    ),
    planBox
  );
}

function renderPlan(container, plan) {
  const v = plan.volumes || {};
  const meta = v.meta_rows || {};
  const rows = [
    ["status 行数", fmtInt(v.status_rows)],
    ["lerobot_episode 行数", fmtInt(v.lerobot_episode_rows)],
    ["预估 episode 行数", fmtInt(v.episode_rows_est)],
    ...Object.entries(meta).map(([k, val]) => [
      `${META_LABELS[k] || k} 行数`,
      fmtInt(val),
    ]),
    ["采样 lerobot UUID 数", fmtInt((plan.lerobot_uuids || []).length)],
  ];
  const table = el(
    "table",
    { class: "kv-table" },
    el("thead", {}, el("tr", {}, el("th", { text: "项目" }), el("th", { text: "数量" }))),
    el("tbody", {}, rows.map(([k, val]) =>
      el("tr", {}, el("td", { text: k }), el("td", { text: val }))
    ))
  );
  const warnings = (plan.warnings || []).map((w) => warningBanner(w));
  replaceChildren(
    container,
    el("h3", { class: "card-title" }, `复制计划（共 ${plan.n} 个 lerobot）`),
    ...warnings,
    table
  );
}

/* ---------- step 3: execute ---------- */

function renderStep3() {
  state.result = null;
  const forceInput = el("input", { type: "checkbox" });
  const resultBox = el("div", { style: "margin-top: 16px" });

  const confirmBtn = el(
    "button",
    { class: "btn btn-danger", type: "button" },
    "确认重置测试环境"
  );

  confirmBtn.addEventListener("click", async () => {
    if (state.executing || !state.plan) return;
    state.executing = true;
    confirmBtn.disabled = true;
    replaceChildren(confirmBtn, spinner(true), " 正在重置…");
    replaceChildren(resultBox);
    try {
      const result = await api.resetExecute({
        datasetNames: [...state.selected],
        n: state.n,
        lerobotUuids: state.plan.lerobot_uuids || [],
        force: forceInput.checked,
      });
      state.result = result;
      renderResult(resultBox, result);
    } catch (err) {
      replaceChildren(resultBox, errorBanner(err));
    } finally {
      state.executing = false;
      confirmBtn.disabled = false;
      replaceChildren(confirmBtn, "确认重置测试环境");
    }
  });

  replaceChildren(
    stepBox,
    el("p", { class: "hint hint-danger" },
      "警告：该操作将 DROP 测试库中所有相关表并重新创建，数据不可恢复，请确认后执行。"
    ),
    el("label", { class: "checkbox-item" },
      forceInput,
      el("span", { text: "强制执行（force=true：跳过数据量上限保护）" })
    ),
    el("div", { class: "wizard-actions" },
      el("button", {
        class: "btn", type: "button",
        onclick: () => {
          state.step = 2;
          renderIndicator();
          renderStep2();
        },
      }, "上一步"),
      confirmBtn
    ),
    resultBox
  );
}

function renderResult(container, result) {
  const copied = result.copied || {};
  const copiedRows = Object.entries(copied).map(([k, val]) =>
    el("tr", {}, el("td", { text: COPIED_LABELS[k] || k }), el("td", { text: fmtInt(val) }))
  );
  const chips = Object.entries(result.status_statistic || {}).map(([k, val]) =>
    el("span", { class: "badge badge-mono", text: `${k}=${fmtInt(val)}` })
  );
  replaceChildren(
    container,
    el("div", { class: "card" },
      el("h3", { class: "card-title" }, result.ok ? "重置完成" : "重置失败"),
      el("p", { class: "hint" },
        `耗时 ${fmtHours(result.duration_s)} 秒 · 删除表 ${fmtInt((result.dropped_tables || []).length)} 张 · 新建表 ${fmtInt((result.created_tables || []).length)} 张`
      ),
      copiedRows.length
        ? el("table", { class: "kv-table" },
            el("thead", {}, el("tr", {}, el("th", { text: "表" }), el("th", { text: "复制行数" }))),
            el("tbody", {}, copiedRows))
        : null,
      chips.length
        ? el("div", {}, el("h3", { class: "card-title" }, "status_statistic"),
            el("div", { class: "chip-row" }, chips))
        : null
    )
  );
}

export function activate(root) {
  if (state.inited) return;
  state.inited = true;

  indicatorBox = el("div", { class: "steps-indicator" });
  stepBox = el("div", { class: "card" });

  replaceChildren(root, indicatorBox, stepBox);
  renderIndicator();
  loadDatasets();
}
