"""Publish matched attendance to learn.* for the student self-view (Jax item #4141, Milan 2026-11-17).

Runs after the nightly autosync. For each course listed in LEARN_COHORTS it writes, per roster
student with an email and per session held: minutes and status into Supabase
public.cfa_learn_attendance (upsert on email+cohort+session_date) and the held dates into
public.cfa_learn_attendance_sessions. Unmatched Zoom names (SkippedParticipant) are never read, and unsure
(unconfirmed) matches count as not seen until someone confirms them.

Status rule (same as Deborah's Oct 20 spreadsheet): present = 60+ minutes, partial = 1-59,
absent = not seen; excused only when Attendance.excused is set (Deborah marks it).
Env: DATABASE_URL (app DB), SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY (cfa-website project).
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

from models import Course, Student, Session, Attendance

PRESENT_MINUTES = 60
# attendance-app course name -> (learn cohort slug, title shown to the student)
LEARN_COHORTS = {
    "Explorations Fall 2026-27": ("explorations-fall-2026-2027", "Explorations, Fall 2026–2027 cohort"),
}


def status_for(minutes, excused):
    if excused:
        return "excused"
    if minutes >= PRESENT_MINUTES:
        return "present"
    return "partial" if minutes > 0 else "absent"


def build_rows():
    rows, held = [], []
    now = datetime.now(timezone.utc).isoformat()
    for course_name, (cohort, title) in LEARN_COHORTS.items():
        course = Course.query.filter_by(name=course_name).first()
        if not course:
            print(f"skip: no course named {course_name!r}", file=sys.stderr)
            continue
        sessions = [s for s in Session.query.filter_by(course_id=course.id).all() if s.session_date]
        students = [s for s in Student.query.filter_by(course_id=course.id).all() if (s.email or "").strip()]
        for sess in sessions:
            held.append({"cohort": cohort, "session_date": sess.session_date.isoformat(), "label": sess.label})
            all_att = Attendance.query.filter_by(session_id=sess.id).all()
            # Minutes count only for confirmed matches; an unsure match is not shown to a student.
            att = {a.student_id: a for a in all_att if a.confirmed}
            excused_ids = {a.student_id for a in all_att if getattr(a, "excused", False)}
            for st in students:
                a = att.get(st.id)
                minutes = (a.total_minutes or 0) if a else 0
                excused = st.id in excused_ids
                rows.append({
                    "email": st.email.strip().lower(), "cohort": cohort, "cohort_title": title,
                    "session_date": sess.session_date.isoformat(), "minutes": minutes,
                    "status": status_for(minutes, excused), "updated_at": now,
                })
    return rows, held


def upsert(table, rows, conflict):
    if not rows:
        return
    url = f"{os.environ['SUPABASE_URL']}/rest/v1/{table}?on_conflict={conflict}"
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    req = urllib.request.Request(url, data=json.dumps(rows).encode(), method="POST", headers={
        "apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()


def publish(dry=False):
    """Call inside an app context (the cron route already has one)."""
    rows, held = build_rows()
    result = {"rows": len(rows), "sessions": len(held), "dry_run": dry}
    if not dry:
        upsert("cfa_learn_attendance_sessions", held, "cohort,session_date")
        upsert("cfa_learn_attendance", rows, "email,cohort,session_date")
    return result


def main():
    from app import create_app
    with create_app().app_context():
        print(json.dumps(publish(dry="--dry-run" in sys.argv)))


if __name__ == "__main__":
    main()
