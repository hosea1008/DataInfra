"""Reset-test-environment wizard service.

All logic for the /api/reset/* endpoints lives here; the router only does
HTTP validation and error translation. PRODUCTION is read-only (reader user +
default_transaction_read_only=on) and receives ZERO writes from this module.
execute_reset runs the whole TEST rebuild (DROP SCHEMA -> CREATE TABLEs ->
COPY -> status_statistic recompute) in ONE transaction on a manually managed
pooled test connection (getconn/putconn + explicit commit/rollback), so a
mid-reset failure never leaves a half-built schema. Recreated tables carry
columns, types, nullability and defaults only - no PK/UNIQUE/index/FKs.
"""

import tempfile
import time
from dataclasses import dataclass

from psycopg2.extensions import cursor as PgCursor

from app.config import get_conn_params
from app.db import PoolManager
from app.schemas import (
    DatasetCount,
    ResetDatasetsResponse,
    ResetExecuteRequest,
    ResetExecuteResponse,
    ResetPlanRequest,
    ResetPlanResponse,
    ResetVolumes,
)
from app.sqlutil import fetch_table_columns, fetch_table_names, qident

DEFAULT_N = 10

#: Cap on the estimated episode-row volume; execute above it needs force=true.
MAX_COPY_ROWS_EST = 200_000

#: Metadata tables are copied in full; these are sampled by lerobot_uuid.
META_TABLES = ("step_key", "version_info", "column_info")
SAMPLED_TABLES = ("status", "lerobot_episode", "data")

#: SpooledTemporaryFile rollover threshold for COPY payloads.
_SPOOL_MAX_BYTES = 16 * 1024 * 1024


class ResetValidationError(Exception):
    """Invalid reset request (unknown dataset/uuid, cap exceeded) -> HTTP 400."""


class ResetConfigError(Exception):
    """Dangerous server configuration (test target == production) -> HTTP 500."""


def env_targets_equal(prod_params: dict, test_params: dict) -> bool:
    """True when both envs resolve to the same database (host, port, dbname)."""
    keys = ("host", "port", "dbname")
    return all(prod_params.get(k) == test_params.get(k) for k in keys)


def assert_reset_target_safe() -> None:
    """Refuse to run a reset when test_env points at the production database."""
    if env_targets_equal(get_conn_params("production"), get_conn_params("test")):
        raise ResetConfigError(
            "test_env and production_env resolve to the same database; "
            "refusing to reset"
        )


@dataclass(frozen=True, slots=True)
class _Cursors:
    """The two cursors a COPY travels between: production source, test target."""

    prod: PgCursor
    test: PgCursor


def list_datasets(pools: PoolManager) -> ResetDatasetsResponse:
    """Wizard step 1: production datasets, lerobot counts, default sample size."""
    with pools.cursor("production") as cur:
        cur.execute("SELECT dataset_name, count(*) FROM status GROUP BY 1 ORDER BY 1")
        datasets = [
            DatasetCount(dataset_name=name, lerobot_count=count)
            for name, count in cur.fetchall()
        ]
        return ResetDatasetsResponse(datasets=datasets, default_n=DEFAULT_N)


def build_plan(pools: PoolManager, req: ResetPlanRequest) -> ResetPlanResponse:
    """Wizard step 2: sample n lerobots, estimate copy volumes, warn (reads only)."""
    with pools.cursor("production") as cur:
        if not req.dataset_names:
            raise ResetValidationError("dataset_names must be non-empty")
        cur.execute("SELECT DISTINCT dataset_name FROM status")
        unknown = sorted(set(req.dataset_names) - {row[0] for row in cur.fetchall()})
        if unknown:
            raise ResetValidationError(f"unknown dataset_names: {', '.join(unknown)}")

        cur.execute(
            "SELECT lerobot_uuid FROM status"
            " WHERE dataset_name = ANY(%s) ORDER BY random() LIMIT %s",
            (req.dataset_names, req.n),
        )
        uuids = [row[0] for row in cur.fetchall()]
        volumes = _estimate_volumes(cur, uuids)

        cur.execute(
            "SELECT count(*) FROM status WHERE dataset_name = ANY(%s)",
            (req.dataset_names,),
        )
        available = cur.fetchone()[0]
        warnings = []
        if available < req.n:
            warnings.append(f"only {available} lerobots available (requested {req.n})")
        if volumes.episode_rows_est > MAX_COPY_ROWS_EST:
            warnings.append("estimated episode rows exceed cap; execute requires force=true")
    return ResetPlanResponse(
        dataset_names=req.dataset_names,
        n=req.n,
        lerobot_uuids=uuids,
        volumes=volumes,
        warnings=warnings,
    )


def _estimate_volumes(cur: PgCursor, uuids: list[str]) -> ResetVolumes:
    """Row-volume estimate for the sampled uuids + full meta-table counts."""
    cur.execute(
        "SELECT count(*), coalesce(sum(episode_count), 0)"
        " FROM lerobot_episode WHERE lerobot_uuid = ANY(%s)",
        (uuids,),
    )
    le_rows, episode_est = cur.fetchone()
    meta = {}
    for table in META_TABLES:
        cur.execute(f"SELECT count(*) FROM {qident(table).as_string(cur)}")
        meta[table] = cur.fetchone()[0]
    return ResetVolumes(
        status_rows=len(uuids),
        lerobot_episode_rows=le_rows,
        episode_rows_est=episode_est,
        meta_rows=meta,
    )


def execute_reset(pools: PoolManager, req: ResetExecuteRequest) -> ResetExecuteResponse:
    """Wizard step 3: rebuild the TEST schema from production, copy sampled data."""
    started = time.monotonic()
    assert_reset_target_safe()
    pool = pools.get_pool("test")
    tconn = pool.getconn()
    try:
        with pools.cursor("production") as pcur, tconn.cursor() as tcur:
            _validate_execute(pcur, req)
            tables = fetch_table_names(pcur)
            schema = {t: _introspect_table(pcur, t) for t in tables}

            dropped = fetch_table_names(tcur)
            tcur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            tcur.execute("CREATE SCHEMA public")
            for table in tables:
                tcur.execute(_create_table_sql(tcur, table, schema[table]))

            uuids = list(req.lerobot_uuids)
            channels = _Cursors(prod=pcur, test=tcur)
            copy_plan = [(t, None) for t in META_TABLES]
            copy_plan += [(t, uuids) for t in SAMPLED_TABLES]
            copied = {t: _copy_table(channels, t, u) for t, u in copy_plan if t in schema}
            statistic = _recompute_status_statistic(tcur)
        tconn.commit()
    except Exception:
        tconn.rollback()
        raise
    finally:
        pool.putconn(tconn)
    return ResetExecuteResponse(
        ok=True,
        duration_s=round(time.monotonic() - started, 3),
        dropped_tables=dropped,
        created_tables=tables,
        copied=copied,
        status_statistic=statistic,
    )


def _validate_execute(pcur: PgCursor, req: ResetExecuteRequest) -> None:
    """Contract step (a): every uuid must exist in production status and belong
    to dataset_names; the episode estimate above the cap requires force=true."""
    if not req.lerobot_uuids:
        raise ResetValidationError("lerobot_uuids must be non-empty")
    pcur.execute(
        "SELECT lerobot_uuid, dataset_name FROM status WHERE lerobot_uuid = ANY(%s)",
        (list(req.lerobot_uuids),),
    )
    found = dict(pcur.fetchall())
    missing = sorted(set(req.lerobot_uuids) - found.keys())
    if missing:
        raise ResetValidationError(
            f"uuids not in production status: {', '.join(missing[:5])}"
        )
    foreign = sorted(u for u, ds in found.items() if ds not in set(req.dataset_names))
    if foreign:
        raise ResetValidationError(
            f"uuids outside dataset_names: {', '.join(foreign[:5])}"
        )
    pcur.execute(
        "SELECT coalesce(sum(episode_count), 0)"
        " FROM lerobot_episode WHERE lerobot_uuid = ANY(%s)",
        (list(req.lerobot_uuids),),
    )
    episode_est = pcur.fetchone()[0]
    if episode_est > MAX_COPY_ROWS_EST and not req.force:
        raise ResetValidationError(
            f"estimated episode rows ({episode_est}) exceed cap"
            f" ({MAX_COPY_ROWS_EST}); resubmit with force=true"
        )


def _introspect_table(pcur: PgCursor, table: str) -> list[tuple]:
    """Contract step (b): (name, format_type, attnotnull, default) per column."""
    pcur.execute(
        """
        SELECT a.attname, format_type(a.atttypid, a.atttypmod),
               a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE c.relname = %s AND n.nspname = 'public'
          AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY a.attnum
        """,
        (table,),
    )
    return [tuple(row) for row in pcur.fetchall()]


def _create_table_sql(cur: PgCursor, table: str, columns: list[tuple]) -> str:
    """Contract step (b): CREATE TABLE with types/nullability/defaults only."""
    defs = []
    for name, col_type, not_null, default in columns:
        piece = f"{qident(name).as_string(cur)} {col_type}"
        if not_null:
            piece += " NOT NULL"
        if default is not None:
            piece += f" DEFAULT {default}"
        defs.append(piece)
    return f"CREATE TABLE {qident(table).as_string(cur)} ({', '.join(defs)})"


def _copy_table(channels: _Cursors, table: str, uuids: list[str] | None) -> int:
    """Contract step (d): COPY table prod->test via a spooled temp file; return
    the inserted count. With uuids, one COPY pass restricted to them; else a
    full copy. copy_expert takes no params, so the ANY literal is rendered
    with cursor.mogrify; identifiers go through qident."""
    pcur, tcur = channels.prod, channels.test
    cols = fetch_table_columns(pcur, table)
    cols_sql = ", ".join(qident(c).as_string(pcur) for c in cols)
    source = f"SELECT {cols_sql} FROM {qident(table).as_string(pcur)}"
    copy_out = f"COPY ({source}) TO STDOUT (FORMAT csv)"
    if uuids is not None:
        copy_out = pcur.mogrify(
            f"COPY ({source} WHERE lerobot_uuid = ANY(%s)) TO STDOUT (FORMAT csv)",
            (uuids,),
        ).decode()
    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES) as buf:
        pcur.copy_expert(copy_out, buf)
        buf.seek(0)
        copy_in = (
            f"COPY {qident(table).as_string(tcur)} ({cols_sql}) FROM STDIN (FORMAT csv)"
        )
        tcur.copy_expert(copy_in, buf)
        return tcur.rowcount


def _recompute_status_statistic(tcur: PgCursor) -> dict[str, int]:
    """Contract step (e): rebuild the single status_statistic row from the TEST
    status table; columns are int in status_statistic AND bool in status, each
    value the count of status rows where the flag is true."""
    tcur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'status_statistic'
          AND data_type IN ('integer', 'bigint')
        """
    )
    int_cols = {row[0] for row in tcur.fetchall()}
    tcur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'status'
          AND data_type = 'boolean'
        """
    )
    cols = sorted(int_cols & {row[0] for row in tcur.fetchall()})
    if not cols:
        return {}
    select_list = ", ".join(
        f"count(*) FILTER (WHERE {qident(c).as_string(tcur)}) AS {qident(c).as_string(tcur)}"
        for c in cols
    )
    tcur.execute(f"SELECT {select_list} FROM status")
    counts = dict(zip(cols, tcur.fetchone()))
    tcur.execute("DELETE FROM status_statistic")
    cols_sql = ", ".join(qident(c).as_string(tcur) for c in cols)
    tcur.execute(
        f"INSERT INTO status_statistic ({cols_sql}) VALUES ({', '.join(['%s'] * len(cols))})",
        [counts[c] for c in cols],
    )
    return counts
