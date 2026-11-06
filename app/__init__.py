import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

import sqlite3
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()

def _migrate_match_team_columns(engine):
    if engine.dialect.name != "sqlite":
        return

    with engine.connect() as connection:
        columns = connection.exec_driver_sql('PRAGMA table_info("match")').mappings().all()
        if not columns:
            return

        nullable = {column["name"]: not column["notnull"] for column in columns}
        if nullable.get("home_team_id") and nullable.get("away_team_id"):
            return

        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            connection.exec_driver_sql("BEGIN")
            connection.exec_driver_sql(
                """
                CREATE TABLE match_new (
                    id INTEGER NOT NULL,
                    round_id INTEGER NOT NULL,
                    table_number INTEGER NOT NULL,
                    home_team_id INTEGER,
                    away_team_id INTEGER,
                    PRIMARY KEY (id),
                    UNIQUE (round_id, table_number),
                    UNIQUE (round_id, home_team_id),
                    UNIQUE (round_id, away_team_id),
                    CHECK (home_team_id != away_team_id),
                    FOREIGN KEY(round_id) REFERENCES round (id),
                    FOREIGN KEY(home_team_id) REFERENCES team (id),
                    FOREIGN KEY(away_team_id) REFERENCES team (id)
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO match_new (
                    id, round_id, table_number, home_team_id, away_team_id
                )
                SELECT id, round_id, table_number, home_team_id, away_team_id
                FROM "match"
                """
            )
            connection.exec_driver_sql('DROP TABLE "match"')
            connection.exec_driver_sql("ALTER TABLE match_new RENAME TO match")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()

@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

def create_app():
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    app = Flask(__name__, instance_relative_config=True)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///league.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY must be set in the environment or .env file")

    os.makedirs(app.instance_path, exist_ok = True)
    db.init_app(app)

    from . import models
    from .routes import bp

    app.register_blueprint(bp)
    with app.app_context():
        db.create_all()
        _migrate_match_team_columns(db.engine)

    return app