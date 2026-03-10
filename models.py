from datetime import datetime, timezone
from extensions import db


class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Text, nullable=False)
    zoom_meeting_id = db.Column(db.Text)  # Zoom room meeting ID for API sync
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    students = db.relationship("Student", backref="course", cascade="all, delete-orphan")
    sessions = db.relationship("Session", backref="course", cascade="all, delete-orphan")
    aliases = db.relationship("Alias", backref="course", cascade="all, delete-orphan")


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    name = db.Column(db.Text, nullable=False)
    email = db.Column(db.Text)
    phone = db.Column(db.Text)
    __table_args__ = (db.UniqueConstraint("course_id", "name"),)


class Session(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    label = db.Column(db.Text)
    zoom_topic = db.Column(db.Text)
    session_date = db.Column(db.Date)
    duration_minutes = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    participants = db.relationship(
        "ZoomParticipant", backref="session", cascade="all, delete-orphan"
    )
    attendances = db.relationship(
        "Attendance", backref="session", cascade="all, delete-orphan"
    )
    skipped = db.relationship(
        "SkippedParticipant", backref="session", cascade="all, delete-orphan"
    )


class ZoomParticipant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session.id"), nullable=False)
    raw_name = db.Column(db.Text, nullable=False)
    email = db.Column(db.Text)
    duration_minutes = db.Column(db.Integer, default=0)


class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    total_minutes = db.Column(db.Integer, default=0)
    match_confidence = db.Column(db.Float)
    match_method = db.Column(db.Text)
    confirmed = db.Column(db.Boolean, default=False)
    student = db.relationship("Student")
    __table_args__ = (db.UniqueConstraint("session_id", "student_id"),)


class SkippedParticipant(db.Model):
    """Zoom participants explicitly skipped during review — preserved for re-review."""
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("session.id"), nullable=False)
    cleaned_name = db.Column(db.Text, nullable=False)
    aliases = db.Column(db.Text)  # pipe-separated
    total_minutes = db.Column(db.Integer, default=0)
    raw_names = db.Column(db.Text)  # pipe-separated


class Alias(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("student.id"), nullable=False)
    alias_name = db.Column(db.Text, nullable=False)
    student = db.relationship("Student")
    __table_args__ = (db.UniqueConstraint("course_id", "alias_name"),)
