import os
from flask import Flask
from extensions import db


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-key-change-in-prod")
    basedir = os.path.abspath(os.path.dirname(__file__))
    db_path = os.path.join(basedir, "instance", "cfa_attendance.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", f"sqlite:///{db_path}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB upload limit

    db.init_app(app)

    from routes.courses import courses_bp
    from routes.sessions import sessions_bp
    from routes.reports import reports_bp

    app.register_blueprint(courses_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(reports_bp)

    with app.app_context():
        import models  # noqa: F401

        db.create_all()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
