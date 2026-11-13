"""Validation and transactional updates for the standalone EFS path script."""

import csv
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest

from app.config import get_conn_params
from scripts import migrate_efs_paths as migrate


def test_script_location_and_bounded_grouped_verification(tmp_path, monkeypatch):
    assert migrate.DEFAULT_DBINFO == Path(__file__).resolve().parents[2] / "DBInfo.json"
    calls = []

    def verify(item):
        calls.append((item.old_path, item.new_path))
        if item.old_path == "bad":
            raise migrate.VerificationError("different file sizes")

    monkeypatch.setattr(migrate, "verify_candidate", verify)
    items = [migrate.Candidate(str(n), "good", "new") for n in range(100)]
    items += [migrate.Candidate("bad-1", "bad", "other"), migrate.Candidate("bad-2", "bad", "other")]
    report = tmp_path / "results.csv"
    updates = []
    failed = migrate.verify_all(items, workers=2, report_path=report,
                                progress=lambda *args: updates.append(args))
    assert failed == 2
    assert len(calls) == 2  # identical paths are compared only once
    assert updates[-1][:3] == (102, 102, 2)
    with report.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 102
    assert sum(row["result"] == "FAILED" for row in rows) == 2


def _pair(tmp_path: Path):
    old = tmp_path / "efs-mi-east4-hpc" / "robot"
    new = tmp_path / "efs-mi-east4-4" / "robot"
    for path in (old, new):
        (path / "meta").mkdir(parents=True)
        (path / "meta" / "large.bin").write_bytes(b"abc" * 10000)
    return old, new


def test_directory_names_and_file_sizes_only(tmp_path):
    old, new = _pair(tmp_path)
    item = migrate.Candidate(str(uuid4()), str(old), str(new))
    migrate.verify_candidate(item)
    (new / "meta" / "large.bin").write_bytes(b"abd" * 10000)
    assert migrate.verify_candidate(item) == "name-size"  # same length; bytes are intentionally ignored
    (new / "meta" / "large.bin").write_bytes(b"abcd" * 10000)
    with pytest.raises(migrate.VerificationError, match="size differs"):
        migrate.verify_candidate(item)
    (new / "meta" / "large.bin").write_bytes(b"abc" * 10000)
    (new / "meta" / "extra").write_text("unexpected")
    with pytest.raises(migrate.VerificationError, match="entries differ"):
        migrate.verify_candidate(item)
    (new / "meta" / "extra").unlink()
    (new / "meta" / "large.bin").unlink()
    with pytest.raises(migrate.VerificationError, match="entries differ"):
        migrate.verify_candidate(item)


def test_missing_empty_and_same_physical_directory(tmp_path):
    old, new = _pair(tmp_path)
    item = migrate.Candidate(str(uuid4()), str(old), str(new))
    for file in (new / "meta").iterdir():
        file.unlink()
    (new / "meta").rmdir()
    with pytest.raises(migrate.VerificationError, match="empty"):
        migrate.verify_candidate(item)
    new.rmdir()
    with pytest.raises(migrate.VerificationError):
        migrate.verify_candidate(item)
    new.symlink_to(old, target_is_directory=True)
    migrate.verify_candidate(item)


def test_three_table_update_and_stale_status_rollback(tmp_path):
    old, new = _pair(tmp_path)
    uuid = str(uuid4())
    with psycopg2.connect(**get_conn_params("test")) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE status (lerobot_uuid uuid, lerobot_path text)")
            cur.execute("CREATE TEMP TABLE lerobot_episode (lerobot_uuid uuid, lerobot_path text)")
            cur.execute("CREATE TEMP TABLE data (lerobot_uuid uuid, lerobot_path text)")
            cur.execute("SELECT nspname FROM pg_namespace WHERE oid = pg_my_temp_schema()")
            schema = cur.fetchone()[0]
            for table, n in (("status", 1), ("lerobot_episode", 1), ("data", 2)):
                for _ in range(n):
                    cur.execute(f"INSERT INTO {table} VALUES (%s::uuid, %s)", (uuid, str(old)))
            candidates = migrate.find_candidates(cur, schema)
        assert candidates == [migrate.Candidate(uuid, str(old), str(new))]
        migrate.verify_candidate(candidates[0])
        timeouts = []

        def inspect_timeouts(table, count):
            if table == "status" and count is None:
                with conn.cursor() as cur:
                    cur.execute("SELECT current_setting('statement_timeout'), "
                                "current_setting('lock_timeout')")
                    timeouts.append(cur.fetchone())

        assert migrate.apply_updates(conn, candidates, schema, progress=inspect_timeouts) == {
            "status": 1, "lerobot_episode": 1, "data": 2,
        }
        assert timeouts == [("0", "1min")]
        with conn.cursor() as cur:
            for table, n in (("status", 1), ("lerobot_episode", 1), ("data", 2)):
                cur.execute(f"SELECT lerobot_path FROM {table}")
                assert cur.fetchall() == [(str(new),)] * n
            cur.execute("UPDATE status SET lerobot_path = %s", (str(old),))
        with pytest.raises(migrate.VerificationError, match="changed since verification"):
            migrate.apply_updates(conn, [migrate.Candidate(uuid, str(new), str(old))], schema)
        with conn.cursor() as cur:
            cur.execute("SELECT lerobot_path FROM lerobot_episode")
            assert cur.fetchone()[0] == str(new)


def test_convert_path_changes_only_when_it_matches_old_row_path(tmp_path):
    old, new = _pair(tmp_path)
    uuid, unrelated_uuid = str(uuid4()), str(uuid4())
    convert_column = migrate.CONVERT_PATH_COLUMN
    with psycopg2.connect(**get_conn_params("test")) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE status (lerobot_uuid uuid, lerobot_path text)")
            cur.execute("CREATE TEMP TABLE lerobot_episode (lerobot_uuid uuid, lerobot_path text)")
            cur.execute(f"CREATE TEMP TABLE data (id int, lerobot_uuid uuid, lerobot_path text, {convert_column} text)")
            cur.execute("SELECT nspname FROM pg_namespace WHERE oid = pg_my_temp_schema()")
            schema = cur.fetchone()[0]
            cur.execute("INSERT INTO status VALUES (%s::uuid, %s)", (uuid, str(old)))
            cur.execute("INSERT INTO lerobot_episode VALUES (%s::uuid, %s)", (uuid, str(old)))
            rows = [
                (1, uuid, str(old), str(old)),
                (2, uuid, str(old), "manual-path"),
                (3, uuid, str(old), None),
                (4, uuid, str(new), str(old)),
                (5, unrelated_uuid, str(old), str(old)),
            ]
            for row_id, row_uuid, path, converted in rows:
                cur.execute(f"INSERT INTO data VALUES (%s, %s::uuid, %s, %s)",
                            (row_id, row_uuid, path, converted))
            candidates = migrate.find_candidates(cur, schema)
        assert migrate.apply_updates(conn, candidates, schema) == {
            "status": 1, "lerobot_episode": 1, "data": 3,
        }
        with conn.cursor() as cur:
            cur.execute(f"SELECT id, lerobot_path, {convert_column} FROM data ORDER BY id")
            assert cur.fetchall() == [
                (1, str(new), str(new)),
                (2, str(new), "manual-path"),
                (3, str(new), None),
                (4, str(new), str(old)),
                (5, str(old), str(old)),
            ]


def test_disconnected_rollback_does_not_hide_original_error(capsys):
    class DisconnectedConnection:
        autocommit = False
        closed = 1

        def cursor(self):
            raise psycopg2.OperationalError("original connection failure")

        def rollback(self):
            raise AssertionError("rollback must not be attempted on a closed connection")

    item = migrate.Candidate(str(uuid4()), "/old", "/new")
    with pytest.raises(psycopg2.OperationalError, match="original connection failure"):
        migrate.apply_updates(DisconnectedConnection(), [item])
    assert "original connection failure" in capsys.readouterr().err
