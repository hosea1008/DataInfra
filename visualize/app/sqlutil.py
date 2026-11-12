"""SQL safety utilities: identifier validation, truncation, serialization.

Identifiers (table/column names) must ALWAYS go through ``validate_identifier``
(or ``qident``) and psycopg2.sql composition - never through string formatting.
The helpers here are the whitelist layer used by the routers/services added in
later tasks.
"""

import datetime
import re
from decimal import Decimal
from typing import Any

from psycopg2 import sql
from psycopg2.extensions import cursor as PgCursor

IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

#: Default max length for :func:`truncate_value`.
DEFAULT_MAX_LEN = 500


def validate_identifier(name: str) -> str:
    """Return ``name`` unchanged if it is a safe SQL identifier.

    Only lowercase ``[a-z_][a-z0-9_]*`` names are accepted (the naming
    convention of every table/column in this project). Raises ValueError
    otherwise - this is the injection guard for all dynamic SQL.
    """
    if not isinstance(name, str) or not IDENT_RE.match(name):
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return name


def qident(name: str) -> sql.Identifier:
    """Validate ``name`` and return a psycopg2 ``sql.Identifier`` for composition."""
    return sql.Identifier(validate_identifier(name))


def fetch_table_columns(cur: PgCursor, table: str) -> list[str]:
    """Return the live column names of ``table`` in the ``public`` schema.

    Reads pg_catalog (pg_attribute / pg_class / pg_namespace), excluding system
    columns (attnum > 0) and dropped columns. Used to build column whitelists.
    """
    validate_identifier(table)
    cur.execute(
        """
        SELECT a.attname
        FROM pg_attribute a
        JOIN pg_class c ON c.oid = a.attrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = %s
          AND n.nspname = 'public'
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY a.attnum
        """,
        (table,),
    )
    return [row[0] for row in cur.fetchall()]


def fetch_table_names(cur: PgCursor) -> list[str]:
    """Return all table names in the ``public`` schema, sorted."""
    cur.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    )
    return [row[0] for row in cur.fetchall()]


def truncate_value(v: Any, max_len: int = DEFAULT_MAX_LEN) -> dict:
    """Truncate a display value to ``max_len`` characters.

    Returns ``{"v": <value>, "t": <truncated>}``:
    - str longer than ``max_len`` -> ``{"v": v[:max_len], "t": True}``
    - anything else (incl. None and short str) -> ``{"v": v, "t": False}``
    """
    if isinstance(v, str) and len(v) > max_len:
        return {"v": v[:max_len], "t": True}
    return {"v": v, "t": False}


def truncate_row(values: Any, max_len: int = DEFAULT_MAX_LEN) -> list[dict]:
    """Apply :func:`truncate_value` to each value of a row sequence."""
    return [truncate_value(v, max_len) for v in values]


def serialize_cell(v: Any) -> Any:
    """Convert a DB cell value to a JSON-safe value.

    - datetime/date/time -> ISO 8601 string
    - Decimal -> float
    - bytes-like -> ``"\\x<hex>"`` preview (first 16 bytes; a suffix notes the
      full length when the value is longer)
    - everything else is returned unchanged
    """
    if isinstance(v, (datetime.datetime, datetime.date, datetime.time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        raw = bytes(v)
        preview = raw[:16].hex()
        if len(raw) > 16:
            return f"\\x{preview}...({len(raw)} bytes)"
        return f"\\x{preview}"
    return v
