"""The migration must build exactly the schema the models describe, on SQLite and on Postgres."""
from __future__ import annotations

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, Engine, ForeignKeyConstraint, UniqueConstraint, create_engine, inspect

from app.models import Base


def user_tables(url: str) -> set[str]:
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()


def declared_names(kind: type) -> set[str]:
    return {c.name for t in Base.metadata.tables.values() for c in t.constraints if isinstance(c, kind) and c.name}


def test_there_is_a_single_migration_head(alembic_cfg: Config) -> None:
    assert len(ScriptDirectory.from_config(alembic_cfg).get_heads()) == 1


def test_upgrade_creates_every_model_table(db_url: str, alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    assert user_tables(db_url) == set(Base.metadata.tables)


def test_migrated_schema_matches_the_models(engine: Engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_constraint_names_are_the_declared_ones(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = Base.metadata.tables
    unique = {u["name"] for t in tables for u in inspector.get_unique_constraints(t)}
    checks = {c["name"] for t in tables for c in inspector.get_check_constraints(t)}
    foreign = {f["name"] for t in tables for f in inspector.get_foreign_keys(t)}
    assert declared_names(UniqueConstraint) <= unique
    assert declared_names(CheckConstraint) == checks
    assert declared_names(ForeignKeyConstraint) == foreign


def test_downgrade_removes_everything(db_url: str, alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, "base")
    assert user_tables(db_url) == set()


def test_upgrade_is_repeatable(db_url: str, alembic_cfg: Config) -> None:
    command.upgrade(alembic_cfg, "head")
    command.upgrade(alembic_cfg, "head")  # already at head: a no-op, as on every Render restart
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
    assert user_tables(db_url) == set(Base.metadata.tables)
