#!/usr/bin/env python3
"""Verify and migrate EFS lerobot paths in the production database.

Preview (read-only): python scripts/migrate_efs_paths.py
Write after reviewing the preview: python scripts/migrate_efs_paths.py --apply

Any missing, empty, unreadable, or different directory aborts the entire run.
Updates to status, lerobot_episode, and data share one transaction. The script
uses the credentials stored in DBInfo.json rather than the visualization app's
forced reader connection; --apply therefore needs a database writer account.
"""

import argparse
import csv
import json
import os
import stat
import sys
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values

OLD_PREFIX = "/efs-mi-east4-hpc/"
NEW_PREFIX = "/efs-mi-east4-4/"
TABLES = ("status", "lerobot_episode", "data")
BUFFER_SIZE = 1024 * 1024
DEFAULT_DBINFO = Path(__file__).resolve().parents[1] / "DBInfo.json"
DEFAULT_WORKERS = 4
MAX_WARNINGS_SHOWN = 20


class VerificationError(Exception):
    """A path could not be verified; no database updates may be made."""


@dataclass(frozen=True)
class Candidate:
    uuid: str
    old_path: str
    new_path: str


def _same_file(left: str, right: str, left_stat, right_stat) -> None:
    if (left_stat.st_dev, left_stat.st_ino) == (right_stat.st_dev, right_stat.st_ino):
        return
    if left_stat.st_size != right_stat.st_size:
        raise VerificationError(f"file size differs: {left} <> {right}")
    with open(left, "rb") as source, open(right, "rb") as target:
        while True:
            a = source.read(BUFFER_SIZE)
            b = target.read(BUFFER_SIZE)
            if a != b:
                raise VerificationError(f"file content differs: {left} <> {right}")
            if not a:
                break
    # Detect a file being changed while it was being compared.
    for path, before in ((left, left_stat), (right, right_stat)):
        after = os.stat(path)
        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
            raise VerificationError(f"file changed during verification: {path}")


def _same_tree(left: str, right: str, left_ancestors: set, right_ancestors: set) -> None:
    left_stat, right_stat = os.stat(left), os.stat(right)  # follows symlinks
    left_dir, right_dir = stat.S_ISDIR(left_stat.st_mode), stat.S_ISDIR(right_stat.st_mode)
    if left_dir != right_dir:
        raise VerificationError(f"file type differs: {left} <> {right}")
    if not left_dir:
        if not stat.S_ISREG(left_stat.st_mode) or not stat.S_ISREG(right_stat.st_mode):
            raise VerificationError(f"unsupported file type: {left} <> {right}")
        _same_file(left, right, left_stat, right_stat)
        return

    left_id = (left_stat.st_dev, left_stat.st_ino)
    right_id = (right_stat.st_dev, right_stat.st_ino)
    if left_id in left_ancestors or right_id in right_ancestors:
        raise VerificationError(f"directory symlink loop: {left} <> {right}")
    if left_id == right_id:
        return  # both names resolve to the same physical directory
    with os.scandir(left) as iterator:
        left_names = {entry.name for entry in iterator}
    with os.scandir(right) as iterator:
        right_names = {entry.name for entry in iterator}
    if left_names != right_names:
        raise VerificationError(f"directory entries differ: {left} <> {right}")
    for name in sorted(left_names):
        _same_tree(
            os.path.join(left, name), os.path.join(right, name),
            left_ancestors | {left_id}, right_ancestors | {right_id},
        )


def verify_candidate(candidate: Candidate) -> None:
    """Check both lerobot directories and compare every nested file's bytes."""
    left, right = candidate.old_path, candidate.new_path
    if not os.path.isabs(left) or not os.path.isabs(right):
        raise VerificationError("both lerobot paths must be absolute")
    try:
        if not stat.S_ISDIR(os.stat(left).st_mode):
            raise VerificationError(f"original is not a directory: {left}")
        if not stat.S_ISDIR(os.stat(right).st_mode):
            raise VerificationError(f"replacement is not a directory: {right}")
        with os.scandir(right) as iterator:
            if next(iterator, None) is None:
                raise VerificationError(f"replacement directory is empty: {right}")
        _same_tree(left, right, set(), set())
    except (OSError, RecursionError) as exc:
        raise VerificationError(f"cannot verify {left} <> {right}: {exc}") from exc


def find_candidates(cur, schema: str = "public") -> list[Candidate]:
    cur.execute(
        sql.SQL("SELECT lerobot_uuid::text, lerobot_path FROM {}.status "
                "WHERE strpos(lerobot_path, %s) > 0")
        .format(sql.Identifier(schema)),
        (OLD_PREFIX,),
    )
    candidates = []
    seen = set()
    for uuid, old_path in cur:
        if uuid in seen:
            raise VerificationError(f"duplicate status lerobot_uuid: {uuid}")
        seen.add(uuid)
        candidates.append(Candidate(uuid, old_path, old_path.replace(OLD_PREFIX, NEW_PREFIX)))
    return candidates


def verify_all(
    candidates: list[Candidate], workers: int = DEFAULT_WORKERS,
    report_path: Path | None = None,
    progress: Callable[[int, int, int, float], None] | None = None,
) -> int:
    """Verify unique path pairs with bounded I/O concurrency; return failures.

    Each UUID still gets a result in the optional CSV. No more than twice the
    worker count is queued, keeping memory bounded even for large inventories.
    """
    grouped: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for item in candidates:
        grouped[(item.old_path, item.new_path)].append(item)
    groups = iter(grouped.values())
    completed = failures = shown = 0
    started = last_progress = time.monotonic()
    report_file = report_path.open("w", encoding="utf-8", newline="") if report_path else None
    try:
        writer = csv.writer(report_file) if report_file else None
        if writer:
            writer.writerow(("lerobot_uuid", "old_path", "new_path", "result", "detail"))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            pending = {}

            def fill_queue() -> None:
                while len(pending) < workers * 2:
                    group = next(groups, None)
                    if group is None:
                        break
                    pending[executor.submit(verify_candidate, group[0])] = group

            fill_queue()
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    group = pending.pop(future)
                    error = None
                    try:
                        future.result()
                    except VerificationError as exc:
                        error = str(exc)
                    completed += len(group)
                    if error:
                        failures += len(group)
                        for item in group:
                            if shown < MAX_WARNINGS_SHOWN:
                                print(f"WARNING {item.uuid}: {error}", file=sys.stderr)
                                shown += 1
                    if writer:
                        for item in group:
                            writer.writerow((item.uuid, item.old_path, item.new_path,
                                             "FAILED" if error else "OK", error or ""))
                now = time.monotonic()
                if progress and (now - last_progress >= 5 or completed == len(candidates)):
                    progress(completed, len(candidates), failures, now - started)
                    last_progress = now
                fill_queue()
        if failures > shown:
            print(f"... {failures - shown} additional failures omitted from console output.", file=sys.stderr)
            if report_path is None:
                print("Run with --report PATH to save every result.", file=sys.stderr)
        return failures
    finally:
        if report_file:
            report_file.close()


def _uuid_type(cur, table: str, schema: str) -> str:
    cur.execute(
        "SELECT a.atttypid = 'uuid'::regtype FROM pg_attribute a "
        "JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = %s AND a.attname = 'lerobot_uuid' "
        "AND NOT a.attisdropped", (schema, table),
    )
    row = cur.fetchone()
    if row is None:
        raise VerificationError(f"missing {schema}.{table}.lerobot_uuid")
    return "uuid" if row[0] else "text"


def apply_updates(
    conn, candidates: list[Candidate], schema: str = "public",
    progress: Callable[[str, int | None], None] | None = None,
) -> dict[str, int]:
    """Lock and recheck status, then update all three tables atomically."""
    if not candidates:
        return {table: 0 for table in TABLES}
    if conn.autocommit:
        raise ValueError("apply_updates requires autocommit=False for atomic updates")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            if cur.fetchone()[0] == "reader":
                raise VerificationError("--apply requires a writer account, not reader")
            cur.execute("SET LOCAL lock_timeout = '5s'")
            cur.execute(
                "CREATE TEMP TABLE efs_path_replacements "
                "(uuid_text text PRIMARY KEY, old_path text NOT NULL, new_path text NOT NULL) "
                "ON COMMIT DROP"
            )
            execute_values(
                cur, "INSERT INTO efs_path_replacements VALUES %s",
                [(item.uuid, item.old_path, item.new_path) for item in candidates],
                page_size=1000,
            )
            cur.execute("ANALYZE efs_path_replacements")
            column_types = {table: _uuid_type(cur, table, schema) for table in TABLES}

            status_match = sql.SQL("s.lerobot_uuid = r.uuid_text{}")
            status_cast = sql.SQL("::uuid") if column_types["status"] == "uuid" else sql.SQL("")
            cur.execute(
                sql.SQL("SELECT s.lerobot_uuid::text, s.lerobot_path FROM {}.status s "
                        "JOIN efs_path_replacements r ON {} "
                        "FOR UPDATE OF s")
                .format(sql.Identifier(schema), status_match.format(status_cast))
            )
            locked = {uuid: path for uuid, path in cur.fetchall()}
            expected = {item.uuid: item.old_path for item in candidates}
            if locked != expected:
                raise VerificationError("status paths changed since verification; no updates made")

            changed = {}
            for table in TABLES:
                if progress:
                    progress(table, None)
                cast = sql.SQL("::uuid") if column_types[table] == "uuid" else sql.SQL("")
                extra = sql.SQL(" AND t.lerobot_path = r.old_path") if table == "status" else sql.SQL("")
                cur.execute(
                    sql.SQL("UPDATE {}.{} AS t SET lerobot_path = r.new_path "
                            "FROM efs_path_replacements AS r "
                            "WHERE t.lerobot_uuid = r.uuid_text{}{} "
                            "AND t.lerobot_path IS DISTINCT FROM r.new_path")
                    .format(sql.Identifier(schema), sql.Identifier(table), cast, extra)
                )
                changed[table] = cur.rowcount
                if progress:
                    progress(table, changed[table])
            if changed["status"] != len(candidates):
                raise VerificationError("status update count mismatch; transaction rolled back")
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise


def connection_params(dbinfo_path: Path) -> dict:
    with dbinfo_path.open(encoding="utf-8") as source:
        info = json.load(source)
    section = info["production_env"]
    return {key: section[key] for key in ("host", "port", "dbname", "user", "password")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify and migrate EFS lerobot paths in production.",
        epilog="Preview: python scripts/migrate_efs_paths.py\n"
               "Write:   python scripts/migrate_efs_paths.py --apply",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dbinfo", type=Path, default=Path(os.environ.get("VISUALIZE_DBINFO", DEFAULT_DBINFO)))
    parser.add_argument("--apply", action="store_true", help="write the verified changes in one transaction")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, choices=range(1, 17),
                        metavar="1-16", help=f"parallel directory checks (default: {DEFAULT_WORKERS})")
    parser.add_argument("--report", type=Path, help="write every UUID and verification result to a CSV file")
    args = parser.parse_args(argv)
    try:
        params = connection_params(args.dbinfo)
        conn = psycopg2.connect(**params)
        try:
            conn.autocommit = True  # don't hold a DB transaction during filesystem comparison
            with conn.cursor() as cur:
                candidates = find_candidates(cur)
            unique_paths = len({(item.old_path, item.new_path) for item in candidates})
            print(f"Found {len(candidates)} matching status rows ({unique_paths} unique path pairs).", flush=True)
            if not candidates:
                return 0
            print(f"Verifying exact content with {args.workers} worker(s)...", flush=True)

            def show_progress(done: int, total: int, failures: int, elapsed: float) -> None:
                rate = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / rate if rate else 0
                print(f"Verified {done:,}/{total:,} ({done / total:.0%}); "
                      f"failed {failures:,}; elapsed {elapsed:.0f}s; ETA {eta:.0f}s", flush=True)

            failures = verify_all(candidates, args.workers, args.report, show_progress)
            if args.report:
                print(f"Detailed report: {args.report}")
            if failures:
                print(f"Aborted: {failures:,} path(s) failed verification; no database rows changed.", file=sys.stderr)
                return 1
            if not args.apply:
                print("All paths verified; no rows changed. Run with --apply to update all three tables.")
                return 0
            conn.autocommit = False

            def show_update(table: str, count: int | None) -> None:
                if count is None:
                    print(f"Updating {table}...", flush=True)
                else:
                    print(f"Updated {table}: {count:,} rows", flush=True)

            changed = apply_updates(conn, candidates, progress=show_update)
            print("Committed updates: " + ", ".join(f"{table}={changed[table]:,}" for table in TABLES))
            return 0
        finally:
            conn.close()
    except (OSError, KeyError, ValueError, psycopg2.Error, VerificationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
