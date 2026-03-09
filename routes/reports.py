import csv
import io
from flask import Blueprint, render_template, Response
from models import Course, Student, Session, Attendance, SkippedParticipant

reports_bp = Blueprint("reports", __name__)


@reports_bp.route("/courses/<int:course_id>/attendance")
def attendance(course_id):
    course = Course.query.get_or_404(course_id)
    students = Student.query.filter_by(course_id=course.id).order_by(Student.name).all()
    sessions = Session.query.filter_by(course_id=course.id).order_by(Session.session_date).all()

    # Build attendance grid: {student_id: {session_id: attendance}}
    grid = {}
    for s in students:
        grid[s.id] = {}

    attendances = Attendance.query.filter(
        Attendance.session_id.in_([sess.id for sess in sessions]),
        Attendance.confirmed == True,  # noqa: E712
    ).all()

    for att in attendances:
        if att.student_id in grid:
            grid[att.student_id][att.session_id] = att

    # Calculate stats
    total_sessions = len(sessions)
    stats = {}
    for s in students:
        present = sum(1 for sess in sessions if sess.id in grid[s.id])
        pct = (present / total_sessions * 100) if total_sessions > 0 else 0
        stats[s.id] = {
            "present": present,
            "total": total_sessions,
            "percentage": pct,
            "below_threshold": pct < 80,
        }

    return render_template(
        "attendance.html",
        course=course,
        students=students,
        sessions=sessions,
        grid=grid,
        stats=stats,
    )


@reports_bp.route("/sessions/<int:session_id>/report")
def session_report(session_id):
    session = Session.query.get_or_404(session_id)
    course = session.course
    students = Student.query.filter_by(course_id=course.id).order_by(Student.name).all()

    # Confirmed present
    attendances = Attendance.query.filter_by(session_id=session.id, confirmed=True).all()
    present_ids = {a.student_id for a in attendances}
    att_by_student = {a.student_id: a for a in attendances}

    present = []
    for s in students:
        if s.id in present_ids:
            a = att_by_student[s.id]
            present.append({"student": s, "minutes": a.total_minutes, "method": a.match_method})

    # Unconfirmed (uncertain)
    unconfirmed = Attendance.query.filter_by(session_id=session.id, confirmed=False).all()
    uncertain = []
    for a in unconfirmed:
        s = Student.query.get(a.student_id)
        if s:
            uncertain.append({"student": s, "minutes": a.total_minutes, "method": a.match_method})

    # Skipped zoom participants
    skipped = SkippedParticipant.query.filter_by(session_id=session.id).all()

    # Absent (on roster, no attendance record at all)
    all_att_ids = present_ids | {a.student_id for a in unconfirmed}
    absent = [s for s in students if s.id not in all_att_ids]

    return render_template(
        "session_report.html",
        session=session,
        course=course,
        present=present,
        uncertain=uncertain,
        skipped=skipped,
        absent=absent,
    )


@reports_bp.route("/courses/<int:course_id>/attendance/export")
def export_attendance(course_id):
    course = Course.query.get_or_404(course_id)
    students = Student.query.filter_by(course_id=course.id).order_by(Student.name).all()
    sessions = Session.query.filter_by(course_id=course.id).order_by(Session.session_date).all()

    # Build grid
    grid = {}
    for s in students:
        grid[s.id] = {}

    attendances = Attendance.query.filter(
        Attendance.session_id.in_([sess.id for sess in sessions]),
        Attendance.confirmed == True,  # noqa: E712
    ).all()

    for att in attendances:
        if att.student_id in grid:
            grid[att.student_id][att.session_id] = att

    total_sessions = len(sessions)

    # Generate CSV
    output = io.StringIO()
    writer = csv.writer(output)

    header = ["Student", "Email"]
    for sess in sessions:
        label = sess.label or (sess.session_date.isoformat() if sess.session_date else f"Session {sess.id}")
        header.append(label)
    header.extend(["Sessions Attended", "Total Sessions", "Attendance %", "Meets 80%"])
    writer.writerow(header)

    for s in students:
        row = [s.name, s.email or ""]
        present = 0
        for sess in sessions:
            att = grid[s.id].get(sess.id)
            if att:
                row.append(f"{att.total_minutes} min")
                present += 1
            else:
                row.append("Absent")
        pct = (present / total_sessions * 100) if total_sessions > 0 else 0
        row.extend([present, total_sessions, f"{pct:.0f}%", "Yes" if pct >= 80 else "No"])
        writer.writerow(row)

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = f'attachment; filename="{course.name} Attendance.csv"'
    return response
