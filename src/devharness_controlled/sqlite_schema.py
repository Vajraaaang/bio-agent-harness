"""Compare all SQLite schema objects against trusted, locally defined DDL."""
from functools import lru_cache
import sqlite3


@lru_cache(maxsize=8)
def _expected_objects(statements):
    # Only the caller's source-code constants are executed, never stored DDL.
    # Let this SQLite version derive its required autoindexes from those DDLs.
    connection=sqlite3.connect(":memory:")
    try:
        for statement in statements:connection.execute(statement)
        return tuple(connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"))
    finally:connection.close()


def require_exact_schema(connection, definitions):
    expected=_expected_objects(tuple(definitions.values()))
    rows=connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchmany(len(expected)+1)
    if tuple(tuple(row) for row in rows)!=expected:
        raise ValueError("Registry schema objects differ from the approved tables, triggers and autoindexes")
