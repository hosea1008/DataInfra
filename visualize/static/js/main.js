/**
 * main.js — tab router + health indicator init.
 */
import { api } from "./api.js";
import * as versionsView from "./view_versions.js";
import * as tablesView from "./view_tables.js";
import * as statsView from "./view_stats.js";
import * as resetView from "./view_reset.js";

const VIEWS = {
  versions: versionsView,
  tables: tablesView,
  stats: statsView,
  reset: resetView,
};

function switchTab(name) {
  const view = VIEWS[name];
  if (!view) return;
  for (const btn of document.querySelectorAll(".tab-btn")) {
    const active = btn.dataset.tab === name;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-selected", String(active));
  }
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.classList.toggle("is-active", panel.id === `tab-${name}`);
  }
  const panel = document.getElementById(`tab-${name}`);
  if (panel) view.activate(panel);
  if (history.replaceState) history.replaceState(null, "", `#${name}`);
}

function initTabs() {
  document.getElementById("tab-nav").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".tab-btn");
    if (btn) switchTab(btn.dataset.tab);
  });
  const fromHash = (location.hash || "").replace("#", "");
  switchTab(VIEWS[fromHash] ? fromHash : "versions");
}

function setHealth(envKey, ok) {
  const item = document.getElementById(`health-${envKey}`);
  if (!item) return;
  const dot = item.querySelector(".health-dot");
  if (!dot) return;
  dot.classList.remove("health-ok", "health-bad", "health-unknown");
  if (ok === true) dot.classList.add("health-ok");
  else if (ok === false) dot.classList.add("health-bad");
  else dot.classList.add("health-unknown");
  const label = envKey === "production" ? "生产库（只读）" : "测试库";
  const stateText = ok === true ? "连接正常" : ok === false ? "连接失败" : "状态未知";
  item.title = `${label}：${stateText}`;
}

async function initHealth() {
  try {
    const data = await api.health();
    setHealth("production", Boolean(data.envs && data.envs.production));
    setHealth("test", Boolean(data.envs && data.envs.test));
  } catch {
    setHealth("production", null);
    setHealth("test", null);
  }
}

initTabs();
initHealth();
