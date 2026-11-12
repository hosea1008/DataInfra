"""Validation and transactional updates for the standalone EFS path script."""

from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest

from app.config import get_conn_params
from scripts import migrate_efs_paths as migrate


def _pair(tmp_path: Path):
    old = tmp_path / "efs-mi-east4-hpc" / "robot"
    new = tmp_path / "efs-mi-east4-4" / "robot"
    for path in (old, new):
        (path / "meta").mkdir(parents=True)
        (path / "meta" / "large.bin").write_bytes(b"abc" * 10000)
    return old, new


def test_full_directory_content_and_symlink(tmp_path):
    old, new = _pair(tmp_path)
    item = migrate.Candidate(str(uuid4()), str(old), str(new))
    migrate.verify_candidate(item)
    (new / "meta" / "large.bin").write_bytes(b"abd" * 10000)
    with pytest.raises(migrate.VerificationError, match="content differs"):
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
        assert migrate.apply_updates(conn, candidates, schema) == {
            "status": 1, "lerobot_episode": 1, "data": 2,
        }
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
