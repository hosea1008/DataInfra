/**
 * view_tables.js — 表预览 tab.
 * Env toggle, table list, paginated preview grid with sticky header.
 */
import { api } from "./api.js";
import { el, replaceChildren, fmtInt } from "./util.js";
import {
  segToggle,
  loadingBlock,
  errorBanner,
  emptyState,
  dataGrid,
  pager,
} from "./components.js";

const PAGE_SIZES = [20, 50, 100, 200];

const state = {
  inited: false,
  env: "production",
  tables: [],
  table: null,
  limit: 50,
  offset: 0,
};

let listBox = null;
let previewBox = null;
let pagerCtl = null;
let sizeSelect = null;

function renderTableList() {
  if (!state.tables.length) {
    replaceChildren(listBox, emptyState("该环境下没有表"));
    return;
  }
  replaceChildren(
    listBox,
    el(
      "ul",
      { class: "table-list" },
      state.tables.map((t) =>
        el(
          "li",
          {},
          el(
            "button",
            {
              type: "button",
              class: t.name === state.table ? "is-active" : "",
              title: t.name,
              onclick: () => selectTable(t.name),
            },
            el("span", { class: "t-name", text: t.name }),
            el(
              "span",
              { class: "t-meta" },
              el("span", {
                class: "badge",
                text: t.est_rows == null ? "行数未知" : `约 ${fmtInt(t.est_rows)} 行`,
              }),
              el("span", { class: "badge", text: `${fmtInt(t.n_columns)} 列` })
            )
          )
        )
      )
    )
  );
}

async function loadTables() {
  replaceChildren(listBox, loadingBlock("加载表列表…"));
  replaceChildren(previewBox, emptyState("请选择一个表查看数据预览"));
  state.table = null;
  try {
    const data = await api.tables(state.env);
    state.tables = data.tables || [];
    renderTableList();
  } catch (err) {
    replaceChildren(listBox, errorBanner(err));
  }
}

async function selectTable(name) {
  state.table = name;
  state.offset = 0;
  renderTableList();
  await loadRows();
}

async function loadRows() {
  replaceChildren(previewBox, loadingBlock(`加载 ${state.table} …`));
  try {
    const data = await api.tableRows(state.env, state.table, {
      limit: state.limit,
      offset: state.offset,
    });
    const rows = data.rows || [];
    if (!rows.length) {
      replaceChildren(
        previewBox,
        state.offset > 0 ? emptyState("该偏移位置没有数据") : emptyState("该表暂无数据")
      );
      renderPager(data);
      return;
    }
    replaceChildren(previewBox, dataGrid(data.columns || [], rows));
    renderPager(data);
  } catch (err) {
    replaceChildren(previewBox, errorBanner(err));
    renderPager(null);
  }
}

function renderPager(data) {
  if (!pagerCtl) return;
  pagerCtl.update({
    limit: state.limit,
    offset: state.offset,
    estRows: data ? data.est_rows : null,
  });
}

function buildPreviewCard() {
  sizeSelect = el(
    "select",
    {
      onchange: () => {
        state.limit = Number(sizeSelect.value);
        state.offset = 0;
        if (state.table) loadRows();
      },
      "aria-label": "每页行数",
    },
    PAGE_SIZES.map((n) => {
      const opt = el("option", { value: String(n) }, `${n} 行/页`);
      if (n === state.limit) opt.selected = true;
      return opt;
    })
  );

  pagerCtl = pager({
    limit: state.limit,
    offset: state.offset,
    estRows: null,
    onPage: (newOffset) => {
      state.offset = Math.max(0, newOffset);
      if (state.table) loadRows();
    },
  });

  const gridHolder = el("div");
  previewBox = gridHolder;

  return el(
    "div",
    { class: "card" },
    el("h3", { class: "card-title" }, "数据预览"),
    el(
      "div",
      { class: "toolbar" },
      el("span", { class: "control-label" }, "每页行数："),
      sizeSelect
    ),
    gridHolder,
    pagerCtl.element
  );
}

export function activate(root) {
  if (state.inited) return;
  state.inited = true;

  const toggle = segToggle(
    [
      { value: "production", label: "生产库" },
      { value: "test", label: "测试库" },
    ],
    state.env,
    (env) => {
      state.env = env;
      state.offset = 0;
      state.table = null;
      renderPager(null);
      loadTables();
    }
  );

  listBox = el("div", { class: "card" }, el("h3", { class: "card-title" }, "表列表"));
  const listCard = listBox;
  const previewCard = buildPreviewCard();

  replaceChildren(
    root,
    el("div", { class: "card toolbar" }, el("span", { class: "control-label" }, "环境："), toggle.element),
    el("div", { class: "tables-layout" }, listCard, previewCard)
  );
  loadTables();
}
