import os

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///league.db"
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = "placeholder-secret-key" # TODO: change this in production

    os.makedirs(app.instance_Path, exist_ok = True)
    db.init_app(app)

    from . import models
    from .routes import bp

    app.register_blueprint(bp)
    with app.app_context():
        db.create_all()

    return app