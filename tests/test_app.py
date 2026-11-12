"""App factory, database setup, and the hand-written schema migration."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

import app as app_package
from app import _migrate_match_team_columns, create_app, db
from app.models import Team


def test_create_app_requires_secret_key(monkeypatch):
    monkeypatch.setattr(app_package, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app({"SQLALCHEMY_DATABASE_URI": "sqlite://"})


def test_test_config_overrides_database(app):
    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite://"
    assert app.config["TESTING"] is True


def test_tables_are_created(app):
    tables = set(db.inspect(db.engine).get_table_names())
    assert {"tournament", "school", "team", "player", "roster_entry", "round", "match", "game", "bye"} <= tables


def test_sqlite_foreign_keys_are_enforced(app):
    assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
    db.session.add(Team(tournament_id=999, school_id=999, team_number=1))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def _old_schema_engine(path):
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE round (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE team (id INTEGER PRIMARY KEY)")
        connection.exec_driver_sql("""
            CREATE TABLE "match" (
                id INTEGER NOT NULL PRIMARY KEY,
                round_id INTEGER NOT NULL REFERENCES round (id),
                table_number INTEGER NOT NULL,
                home_team_id INTEGER NOT NULL REFERENCES team (id),
                away_team_id INTEGER NOT NULL REFERENCES team (id)
            )""")
        connection.exec_driver_sql("INSERT INTO round (id) VALUES (1)")
        connection.exec_driver_sql("INSERT INTO team (id) VALUES (1), (2)")
        connection.exec_driver_sql('INSERT INTO "match" VALUES (1, 1, 1, 1, 2)')
    return engine


def _notnull(engine):
    with engine.connect() as connection:
        rows = connection.exec_driver_sql('PRAGMA table_info("match")').mappings().all()
    return {row["name"]: bool(row["notnull"]) for row in rows}


def test_migration_makes_match_teams_nullable_and_keeps_rows(tmp_path):
    engine = _old_schema_engine(tmp_path / "old.db")
    assert _notnull(engine)["home_team_id"]

    _migrate_match_team_columns(engine)

    notnull = _notnull(engine)
    assert not notnull["home_team_id"] and not notnull["away_team_id"]
    with engine.connect() as connection:
        assert connection.exec_driver_sql('SELECT * FROM "match"').all() == [(1, 1, 1, 1, 2)]
    engine.dispose()


def test_migration_is_a_no_op_when_already_migrated(tmp_path):
    engine = _old_schema_engine(tmp_path / "old.db")
    _migrate_match_team_columns(engine)
    _migrate_match_team_columns(engine)
    assert not _notnull(engine)["home_team_id"]
    engine.dispose()


def test_migration_skips_a_database_without_a_match_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    _migrate_match_team_columns(engine)
    assert _notnull(engine) == {}
    engine.dispose()
