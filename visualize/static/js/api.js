/**
 * api.js — the ONLY module that knows API URLs.
 * Views must never call fetch directly; they call these wrappers.
 * All wrappers throw Error(message) where message carries the API `detail`.
 */

async function request(path, options = {}) {
  const resp = await fetch(path, {
    headers: { Accept: "application/json" },
    ...options,
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (body && body.detail != null) {
        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail);
      }
    } catch {
      /* keep the HTTP-status fallback message */
    }
    const err = new Error(detail);
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

function post(path, payload) {
  return request(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
  });
}

function qs(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

export const api = {
  /** GET /api/health -> {status, envs:{production,test}, time} */
  health: () => request("/api/health"),

  /** GET /api/envs/{env}/tables -> {env, tables:[{name,est_rows,n_columns}]} */
  tables: (env) => request(`/api/envs/${encodeURIComponent(env)}/tables`),

  /**
   * GET /api/envs/{env}/tables/{table}/rows
   * -> {table, columns, rows:[[{v,t}]], est_rows, limit, offset}
   */
  tableRows: (env, table, { limit = 50, offset = 0, orderBy = "", orderDir = "asc" } = {}) =>
    request(
      `/api/envs/${encodeURIComponent(env)}/tables/${encodeURIComponent(table)}/rows` +
        qs({ limit, offset, order_by: orderBy, order_dir: orderDir })
    ),

  /**
   * GET /api/envs/{env}/versions/progress?keys=a,b
   * keys: array of strings; empty/undefined -> omit param (server returns all).
   */
  versionProgress: (env, keys) =>
    request(
      `/api/envs/${encodeURIComponent(env)}/versions/progress` +
        qs({ keys: Array.isArray(keys) && keys.length ? keys.join(",") : undefined })
    ),

  /**
   * GET /api/stats/datasets?refresh=0|1&exact=0|1 (production only).
   * refresh=1: recompute now (re-sampling big tables).
   * exact=1: full unsampled recompute (implies refresh).
   */
  statsDatasets: (refresh = false, exact = false) =>
    request(
      "/api/stats/datasets" +
        qs({ refresh: refresh ? 1 : 0, exact: exact ? 1 : 0 })
    ),

  /** GET /api/stats/columns -> {columns:[{name,kind,note}]} */
  statsColumns: () => request("/api/stats/columns"),

  /**
   * GET /api/stats/column-distribution (refresh/exact as in statsDatasets).
   */
  columnDistribution: ({
    column,
    weight = "rows",
    topK = 12,
    refresh = false,
    exact = false,
  }) =>
    request(
      "/api/stats/column-distribution" +
        qs({
          column,
          weight,
          top_k: topK,
          refresh: refresh ? 1 : 0,
          exact: exact ? 1 : 0,
        })
    ),

  /** GET /api/reset/datasets -> {datasets:[{dataset_name,lerobot_count}], default_n} */
  resetDatasets: () => request("/api/reset/datasets"),

  /** POST /api/reset/plan */
  resetPlan: (datasetNames, n) =>
    post("/api/reset/plan", { dataset_names: datasetNames, n }),

  /** POST /api/reset/execute */
  resetExecute: ({ datasetNames, n, lerobotUuids, force }) =>
    post("/api/reset/execute", {
      dataset_names: datasetNames,
      n,
      lerobot_uuids: lerobotUuids,
      force,
    }),
};
