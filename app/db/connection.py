from psycopg_pool import ConnectionPool

from app.config import DATABASE_URL, DB_POOL_MIN_SIZE, DB_POOL_MAX_SIZE

# The pool is created but not opened here: opening spins up background
# connection workers, and doing that as a side effect of importing this
# module would run before app startup/config is fully in control (and
# would run on every `import app.db.connection`, including in tests that
# only want to read the module). It is opened explicitly via open_pool()
# (called from the FastAPI lifespan on startup) and lazily by
# get_connection() for standalone scripts that never call open_pool().
_pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=DB_POOL_MIN_SIZE,
    max_size=DB_POOL_MAX_SIZE,
    open=False,
)


def open_pool() -> None:
    """Start the connection pool. Safe to call more than once."""
    _pool.open()


def close_pool() -> None:
    """Close the connection pool, closing all pooled connections."""
    _pool.close()


def get_connection():
    """
    Return a context manager yielding a pooled connection.

    Usage is unchanged from before pooling was introduced:

        with get_connection() as conn:
            with conn.cursor() as cur:
                ...

    On clean exit the transaction is committed; on exception it is rolled
    back — identical to the previous `with psycopg.connect(...) as conn:`
    behaviour. The only difference is the connection is returned to the
    pool afterwards instead of being closed.
    """
    # Idempotent: cheap no-op if the pool is already open (e.g. because
    # the FastAPI lifespan already opened it on startup).
    _pool.open()
    return _pool.connection()
