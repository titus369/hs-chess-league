import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from flask_sqlalchemy import SQLAlchemy

import sqlite3
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()

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

    return app