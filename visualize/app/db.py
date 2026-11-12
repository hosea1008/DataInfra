"""Per-env psycopg2 connection pools with per-env failure isolation.

Usage (FastAPI): the app lifespan creates one :class:`PoolManager` and stores
it on ``app.state.pools``; routers/services access it via ``request.app.state.pools``::

    pools = request.app.state.pools
    with pools.cursor("production", dict_cursor=True) as cur:
        cur.execute(...)

Pools are created lazily on first use. If a pool cannot be created (DB down,
bad credentials), the exception is recorded and re-raised to the caller, but
the PoolManager itself stays usable - the other env is unaffected and
:meth:`PoolManager.health_check` reports the env as down.

Session options applied per env:
- production: ``statement_timeout=300000`` and ``default_transaction_read_only=on``
  (hard read-only policy at the session level, on top of the read-only user;
  the 300 s budget lets full-table GROUP BY statistics over the tens-of-millions
  ``data`` table finish instead of being cancelled).
- test: ``statement_timeout=300000``.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg2
from psycopg2.extensions import connection as PgConnection
from psycopg2.extensions import cursor as PgCursor
from psycopg2.extras import DictCursor
from psycopg2.pool import ThreadedConnectionPool

from app.config import ConfigError, get_conn_params

MIN_CONN = 1
MAX_CONN = 5

# env -> libpq `options` startup parameter
_ENV_OPTIONS = {
    "production": "-c statement_timeout=300000 -c default_transaction_read_only=on",
    "test": "-c statement_timeout=300000",
}


class PoolManager:
    """Lazily-created ThreadedConnectionPool per environment.

    Thread-safe: pool creation is guarded by a lock (FastAPI runs sync
    endpoints in a threadpool, so several threads may hit ``get_pool`` at once).
    """

    def __init__(self) -> None:
        self._pools: dict[str, ThreadedConnectionPool] = {}
        self._errors: dict[str, Exception] = {}
        self._lock = threading.Lock()

    def get_pool(self, env: str) -> ThreadedConnectionPool:
        """Return the pool for ``env``, creating it on first call.

        On creation failure the exception is stored (see ``pool_error``) and
        re-raised; the manager itself is not poisoned.
        """
        pool = self._pools.get(env)
        if pool is not None:
            return pool
        with self._lock:
            pool = self._pools.get(env)
            if pool is not None:
                return pool
            try:
                params = get_conn_params(env)
                params["options"] = _ENV_OPTIONS[env]
                pool = ThreadedConnectionPool(MIN_CONN, MAX_CONN, **params)
            except Exception as exc:
                self._errors[env] = exc
                raise
            self._pools[env] = pool
            return pool

    def pool_error(self, env: str) -> Exception | None:
        """Return the recorded pool-creation error for ``env``, if any."""
        return self._errors.get(env)

    @contextmanager
    def connection(self, env: str) -> Iterator[PgConnection]:
        """Yield a pooled connection for ``env``.

        Commits on success, rolls back on error, and always returns the
        connection to its pool.
        """
        pool = self.get_pool(env)
        conn = pool.getconn()
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            pool.putconn(conn)

    @contextmanager
    def cursor(self, env: str, dict_cursor: bool = False) -> Iterator[PgCursor]:
        """Yield a cursor on a pooled connection (same commit/rollback rules).

        With ``dict_cursor=True`` the cursor is a ``psycopg2.extras.DictCursor``
        (rows behave like dicts).
        """
        with self.connection(env) as conn:
            cur = (
                conn.cursor(cursor_factory=DictCursor) if dict_cursor else conn.cursor()
            )
            try:
                yield cur
            finally:
                cur.close()

    def health_check(self, env: str) -> bool:
        """Return True iff ``SELECT 1`` succeeds on ``env``'s pool.

        Only config and psycopg2 failures are swallowed into ``False``;
        anything else still raises.
        """
        try:
            with self.cursor(env) as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except (ConfigError, psycopg2.Error):
            return False

    def close_all(self) -> None:
        """Close every created pool. Errors recorded earlier are kept."""
        with self._lock:
            pools = list(self._pools.values())
            self._pools.clear()
        for pool in pools:
            pool.closeall()
