"""Unexpected schema objects must fail before they affect registry operations."""
import sqlite3
import pytest
from devharness_controlled.authority_store import AuthorityStore
from devharness_controlled.run_registry import RunRegistry


@pytest.mark.parametrize("kind",["authority","run"])
@pytest.mark.parametrize("extra",["index","view"])
def test_all_schema_objects_are_checked(tmp_path,kind,extra):
    if kind=="authority":
        store=AuthorityStore.create(tmp_path.resolve()/"authority","synthetic-authority")
        table="policies";read=store.capture
    else:
        store=RunRegistry(tmp_path.resolve()/"runs")
        table="attempts";read=store.export_checkpoint
    with sqlite3.connect(store.database_path) as connection:
        if extra=="index":connection.execute(f"CREATE UNIQUE INDEX unapproved_constant ON {table} ((1))")
        else:connection.execute(f"CREATE VIEW unapproved_view AS SELECT * FROM {table}")
    with pytest.raises(ValueError,match="schema"):read()
