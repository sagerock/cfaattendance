from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import Course, Student
from roster_parser import parse_roster_csv

courses_bp = Blueprint("courses", __name__)


@courses_bp.route("/")
def index():
    courses = Course.query.order_by(Course.created_at.desc()).all()
    return render_template("index.html", courses=courses)


@courses_bp.route("/courses", methods=["POST"])
def create_course():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Course name is required.", "error")
        return redirect(url_for("courses.index"))
    course = Course(name=name)
    db.session.add(course)
    db.session.commit()
    flash(f"Course '{name}' created.", "success")
    return redirect(url_for("courses.course_detail", course_id=course.id))


@courses_bp.route("/courses/<int:course_id>")
def course_detail(course_id):
    course = Course.query.get_or_404(course_id)
    return render_template("course_detail.html", course=course)


@courses_bp.route("/courses/<int:course_id>/roster", methods=["GET", "POST"])
def upload_roster(course_id):
    course = Course.query.get_or_404(course_id)

    if request.method == "POST":
        file = request.files.get("roster_file")
        if not file or not file.filename:
            flash("Please select a CSV file.", "error")
            return redirect(url_for("courses.upload_roster", course_id=course_id))

        content = file.read().decode("utf-8-sig")
        course_filter = request.form.get("course_filter", "").strip() or None
        students = parse_roster_csv(content, course_filter=course_filter)

        if not students:
            flash("No students found in CSV (check course filter?).", "error")
            return redirect(url_for("courses.upload_roster", course_id=course_id))

        return render_template("upload_roster.html", course=course, preview=students)

    return render_template("upload_roster.html", course=course, preview=None)


@courses_bp.route("/courses/<int:course_id>/roster/confirm", methods=["POST"])
def confirm_roster(course_id):
    course = Course.query.get_or_404(course_id)

    names = request.form.getlist("name")
    emails = request.form.getlist("email")
    phones = request.form.getlist("phone")
    includes = request.form.getlist("include")

    added = 0
    for i, name in enumerate(names):
        if str(i) not in includes:
            continue
        name = name.strip()
        if not name:
            continue
        email = emails[i].strip() if i < len(emails) else ""
        phone = phones[i].strip() if i < len(phones) else ""

        existing = Student.query.filter_by(course_id=course.id, name=name).first()
        if not existing:
            student = Student(course_id=course.id, name=name, email=email or None, phone=phone or None)
            db.session.add(student)
            added += 1

    db.session.commit()
    flash(f"Added {added} students to roster.", "success")
    return redirect(url_for("courses.course_detail", course_id=course.id))


@courses_bp.route("/courses/<int:course_id>/roster/edit", methods=["GET", "POST"])
def edit_roster(course_id):
    course = Course.query.get_or_404(course_id)

    if request.method == "POST":
        student_ids = request.form.getlist("student_id")
        names = request.form.getlist("name")
        emails = request.form.getlist("email")
        phones = request.form.getlist("phone")
        deletes = request.form.getlist("delete")

        for i, sid in enumerate(student_ids):
            student = Student.query.get(int(sid))
            if not student or student.course_id != course.id:
                continue
            if sid in deletes:
                db.session.delete(student)
            else:
                student.name = names[i].strip()
                student.email = emails[i].strip() or None
                student.phone = phones[i].strip() or None

        db.session.commit()
        flash("Roster updated.", "success")
        return redirect(url_for("courses.course_detail", course_id=course.id))

    return render_template("edit_roster.html", course=course)


@courses_bp.route("/courses/<int:course_id>/roster/add", methods=["POST"])
def add_students(course_id):
    course = Course.query.get_or_404(course_id)
    names = request.form.getlist("new_name")
    emails = request.form.getlist("new_email")
    phones = request.form.getlist("new_phone")

    added = 0
    for i, name in enumerate(names):
        name = name.strip()
        if not name:
            continue
        email = emails[i].strip() if i < len(emails) else ""
        phone = phones[i].strip() if i < len(phones) else ""
        existing = Student.query.filter_by(course_id=course.id, name=name).first()
        if not existing:
            db.session.add(Student(
                course_id=course.id, name=name,
                email=email or None, phone=phone or None,
            ))
            added += 1

    db.session.commit()
    flash(f"Added {added} student{'s' if added != 1 else ''}.", "success")
    return redirect(url_for("courses.edit_roster", course_id=course.id))


@courses_bp.route("/courses/<int:course_id>/delete", methods=["POST"])
def delete_course(course_id):
    course = Course.query.get_or_404(course_id)
    db.session.delete(course)
    db.session.commit()
    flash(f"Course '{course.name}' deleted.", "success")
    return redirect(url_for("courses.index"))
