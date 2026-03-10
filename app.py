import os
from flask import Flask, request, session, redirect, url_for, render_template
from extensions import db

AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "cfa")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "attendance")


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-key-change-in-prod")
    basedir = os.path.abspath(os.path.dirname(__file__))
    db_path = os.path.join(basedir, "instance", "cfa_attendance.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    db_url = os.environ.get("DATABASE_URL", f"sqlite:///{db_path}")
    # Railway uses postgres:// but SQLAlchemy requires postgresql://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB upload limit

    db.init_app(app)

    from routes.courses import courses_bp
    from routes.sessions import sessions_bp
    from routes.reports import reports_bp

    app.register_blueprint(courses_bp)
    app.register_blueprint(sessions_bp)
    app.register_blueprint(reports_bp)

    @app.before_request
    def require_login():
        if request.endpoint == "login" or request.endpoint == "static":
            return
        if not session.get("authenticated"):
            return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            if (
                request.form.get("username") == AUTH_USERNAME
                and request.form.get("password") == AUTH_PASSWORD
            ):
                session["authenticated"] = True
                return redirect(url_for("courses.index"))
            error = "Invalid credentials."
        return render_template("login.html", error=error)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    with app.app_context():
        import models  # noqa: F401

        db.create_all()

        # Add new columns to existing tables (db.create_all doesn't alter)
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        course_cols = [c["name"] for c in inspector.get_columns("course")]
        if "zoom_meeting_id" not in course_cols:
            db.session.execute(text("ALTER TABLE course ADD COLUMN zoom_meeting_id TEXT"))
            db.session.commit()

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
