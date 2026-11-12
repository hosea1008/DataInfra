"""Tests for app.sqlutil: identifier safety, truncation, serialization."""

import datetime
from decimal import Decimal

import pytest
from app.sqlutil import (
    qident,
    serialize_cell,
    truncate_row,
    truncate_value,
    validate_identifier,
)
from psycopg2 import sql


@pytest.mark.parametrize(
    "bad",
    ["a; DROP TABLE x", 'table"name', "123abc", "a b", ""],
)
def test_validate_identifier_rejects(bad):
    with pytest.raises(ValueError):
        validate_identifier(bad)


@pytest.mark.parametrize("good", ["data", "s_step_a_v_1_c_score"])
def test_validate_identifier_accepts(good):
    assert validate_identifier(good) == good


def test_qident_returns_sql_identifier():
    ident = qident("data")
    assert isinstance(ident, sql.Identifier)
    assert ident.string == "data"


def test_qident_rejects_bad_identifier():
    with pytest.raises(ValueError):
        qident("bad name")


def test_truncate_value_long_string():
    value = "x" * 600
    out = truncate_value(value)
    assert out["t"] is True
    assert len(out["v"]) == 500
    assert out["v"] == "x" * 500


@pytest.mark.parametrize("value", ["short", None, 42])
def test_truncate_value_passthrough(value):
    assert truncate_value(value) == {"v": value, "t": False}


def test_truncate_row_maps_over_sequence():
    row = ["a" * 600, "ok", None, 7]
    assert truncate_row(row) == [
        {"v": "a" * 500, "t": True},
        {"v": "ok", "t": False},
        {"v": None, "t": False},
        {"v": 7, "t": False},
    ]


def test_serialize_cell_datetime():
    dt = datetime.datetime(2026, 11, 11, 12, 30, 5)
    assert serialize_cell(dt) == dt.isoformat()


def test_serialize_cell_decimal():
    assert serialize_cell(Decimal("1.5")) == 1.5
    assert isinstance(serialize_cell(Decimal("1.5")), float)


def test_serialize_cell_bytes_hex_preview():
    assert serialize_cell(b"\x01\x02") == "\\x0102"


def test_serialize_cell_passthrough():
    assert serialize_cell("abc") == "abc"
    assert serialize_cell(None) is None
    assert serialize_cell(3.14) == 3.14
