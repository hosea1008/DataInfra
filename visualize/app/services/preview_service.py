"""SQL layer for the table-preview feature (routers/tables.py).

All dynamic SQL lives here. Identifiers always go through ``qident``
(whitelist-validated + psycopg2 sql.Identifier composition); values always go
through query parameters. Every preview query carries explicit LIMIT/OFFSET.
"""

from typing import Any

from psycopg2 import sql
from psycopg2.extensions import cursor as PgCursor

from app.sqlutil import (
    fetch_table_columns,
    qident,
    serialize_cell,
    truncate_value,
    validate_identifier,
)


def _est_rows_or_none(reltuples: Any) -> int | None:
    """Convert a pg_class.reltuples value: negative (-1 = never analyzed) -> None."""
    if reltuples is None or reltuples < 0:
        return None
    return int(reltuples)


def list_tables(cur: PgCursor) -> list[dict[str, Any]]:
    """Return public-schema tables with catalog estimates, ordered by name.

    Each entry: ``{"name": str, "est_rows": int | None, "n_columns": int}``
    where est_rows is pg_class.reltuples::bigint (None when negative) and
    n_columns counts non-dropped attributes with attnum > 0.
    """
    cur.execute(
        """
        SELECT t.tablename,
               c.reltuples::bigint,
               (SELECT count(*)
                  FROM pg_attribute a
                 WHERE a.attrelid = c.oid
                   AND a.attnum > 0
                   AND NOT a.attisdropped)
          FROM pg_tables t
          JOIN pg_class c ON c.relname = t.tablename
          JOIN pg_namespace n ON n.oid = c.relnamespace
                             AND n.nspname = t.schemaname
         WHERE t.schemaname = 'public'
         ORDER BY t.tablename
        """
    )
    return [
        {"name": name, "est_rows": _est_rows_or_none(est), "n_columns": int(ncols)}
        for name, est, ncols in cur.fetchall()
    ]


def table_est_rows(cur: PgCursor, table: str) -> int | None:
    """Return pg_class.reltuples::bigint for one public table (None if < 0)."""
    validate_identifier(table)
    cur.execute(
        """
        SELECT c.reltuples::bigint
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relname = %s
           AND n.nspname = 'public'
        """,
        (table,),
    )
    row = cur.fetchone()
    return _est_rows_or_none(row[0]) if row else None


def fetch_preview(
    cur: PgCursor,
    *,
    table: str,
    order_by: str | None,
    order_dir: str,
    limit: int,
    offset: int,
) -> tuple[list[str], list[list[dict]], int | None]:
    """Run the row preview for ``table``; return (columns, cells, est_rows).

    Columns are the table's live columns in attnum order. ``order_by`` must be
    None or one of those columns (the router enforces this against the same
    whitelist); ``order_dir`` must be "asc" or "desc". Each cell is
    ``{"v": serialize_cell(value) truncated, "t": truncated?}``.
    """
    columns = fetch_table_columns(cur, table)
    col_sql = sql.SQL(", ").join(qident(c) for c in columns)
    query = sql.SQL("SELECT {cols} FROM {tbl}").format(
        cols=col_sql, tbl=qident(table)
    )
    if order_by is not None:
        # order_dir is whitelisted to asc/desc by the router before this call.
        direction = sql.SQL(order_dir.upper())
        query += sql.SQL(" ORDER BY {col} {dir}").format(
            col=qident(order_by), dir=direction
        )
    query += sql.SQL(" LIMIT %s OFFSET %s")
    cur.execute(query, (limit, offset))
    rows = [
        [truncate_value(serialize_cell(value)) for value in row]
        for row in cur.fetchall()
    ]
    return columns, rows, table_est_rows(cur, table)
