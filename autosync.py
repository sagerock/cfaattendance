"""Import Zoom meetings into courses without anyone clicking anything.

import_meeting()  -- the one code path that turns a Zoom meeting instance into a Session
                     (used by the manual "Sync from Zoom" button AND the nightly auto-sync).
run_autosync()    -- for every course with a Zoom room, import every recent meeting that
                     (a) is not already a session of that course and (b) looks like a class
                     of THIS course: enough of its participants match the course roster.

Why the roster guard: CfA's Zoom rooms are shared. Room 1 hosts Kairos Cohort '22 on a
Wednesday and something else on a Saturday. Without the guard every meeting in a room
would be imported into every course linked to that room. With it, a meeting lands in a
course only when that course's students were in it, so a joint Kairos session imports into
both cohorts and a Renewal meeting into neither.
"""
import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Course, Student, Session, ZoomParticipant, Attendance, Alias
from matching import consolidate_participants, match_participants_to_roster
import zoom_api

log = logging.getLogger(__name__)

MIN_MATCHED_STUDENTS = 2      # a "class" of this course has at least this many roster matches
MIN_MATCHED_RATIO = 0.25      # ...and they are at least this share of the people who joined


def _match(course, participants):
    consolidated = consolidate_participants(participants)
    students = Student.query.filter_by(course_id=course.id).all()
    aliases = Alias.query.filter_by(course_id=course.id).all()
    matches = match_participants_to_roster(consolidated, students, aliases, course.id)
    return consolidated, matches


def looks_like_this_course(consolidated, matches):
    """True when enough of the meeting's people are on this course's roster."""
    # Only confident matches count toward "this is our class": a fuzzy 'review' hit
    # against a 3-person roster is exactly how a stranger's meeting would sneak in.
    matched = {m["student_id"] for m in matches if m["student_id"] and m["status"] == "auto"}
    if not consolidated:
        return False, 0
    needed = max(MIN_MATCHED_STUDENTS, math.ceil(MIN_MATCHED_RATIO * len(consolidated)))
    return len(matched) >= needed, len(matched)


def _fill_meeting_header(meeting_uuid, meeting_data):
    """Zoom's participants report carries no topic/start time; get them from the meeting report."""
    if meeting_data.get("session_date") and meeting_data.get("topic"):
        return
    try:
        details = zoom_api.get_meeting_details(meeting_uuid)
    except Exception as e:  # header is nice to have; the attendance still counts
        log.warning("meeting details for %s failed: %s", meeting_uuid, e)
        return
    if not meeting_data.get("topic"):
        meeting_data["topic"] = details.get("topic", "")
    if not meeting_data.get("session_date") and details.get("start_time"):
        meeting_data["start_time"] = details["start_time"]
        meeting_data["session_date"] = details["start_time"].date()
    if not meeting_data.get("duration_minutes"):
        meeting_data["duration_minutes"] = details.get("duration_minutes", 0)


def backfill_session_headers():
    """Sessions imported with a meeting_uuid but no date/topic get them now. Returns count fixed."""
    fixed = 0
    for sess in Session.query.filter(Session.meeting_uuid.isnot(None), Session.session_date.is_(None)).all():
        try:
            d = zoom_api.get_meeting_details(sess.meeting_uuid)
        except Exception as e:
            log.warning("backfill %s failed: %s", sess.meeting_uuid, e)
            continue
        if d.get("start_time"):
            sess.session_date = d["start_time"].date()
        if not sess.zoom_topic:
            sess.zoom_topic = d.get("topic", "")
        if not sess.label:
            sess.label = d.get("topic", "")
        if not sess.duration_minutes:
            sess.duration_minutes = d.get("duration_minutes", 0)
        fixed += 1
    if fixed:
        db.session.commit()
    return fixed


def import_meeting(course, meeting_uuid, label="", meeting_data=None, require_fit=False):
    """Create a Session for `course` from one Zoom meeting instance.

    Returns (session, needs_review, info). session is None when nothing was imported;
    info["reason"] says why ("no_participants", "not_this_course", "duplicate").
    """
    if meeting_data is None:
        meeting_data = zoom_api.get_meeting_participants(meeting_uuid)
    _fill_meeting_header(meeting_uuid, meeting_data)

    participants = meeting_data.get("participants") or []
    if not participants:
        return None, False, {"reason": "no_participants"}

    dup = Session.query.filter_by(course_id=course.id, meeting_uuid=meeting_uuid).first()
    if dup:
        return None, False, {"reason": "duplicate", "session_id": dup.id}
    # Sessions imported before meeting_uuid existed: same course, same day, same topic.
    if meeting_data.get("session_date"):
        dup = Session.query.filter_by(
            course_id=course.id,
            session_date=meeting_data["session_date"],
            zoom_topic=meeting_data.get("topic", ""),
        ).filter(Session.meeting_uuid.is_(None)).first()
        if dup:
            dup.meeting_uuid = meeting_uuid
            db.session.commit()
            return None, False, {"reason": "duplicate", "session_id": dup.id}

    consolidated, matches = _match(course, participants)
    fits, matched_count = looks_like_this_course(consolidated, matches)
    if require_fit and not fits:
        return None, False, {"reason": "not_this_course", "matched": matched_count,
                             "people": len(consolidated)}

    session = Session(
        course_id=course.id,
        label=label or meeting_data.get("topic", ""),
        zoom_topic=meeting_data.get("topic", ""),
        session_date=meeting_data.get("session_date"),
        duration_minutes=meeting_data.get("duration_minutes", 0),
        meeting_uuid=meeting_uuid,
    )
    db.session.add(session)
    try:
        db.session.flush()
    except IntegrityError:
        # Another run imported this meeting between our duplicate check and now.
        db.session.rollback()
        dup = Session.query.filter_by(course_id=course.id, meeting_uuid=meeting_uuid).first()
        return None, False, {"reason": "duplicate", "session_id": dup.id if dup else None}

    for p in participants:
        db.session.add(ZoomParticipant(
            session_id=session.id,
            raw_name=p["raw_name"],
            email=p["email"],
            duration_minutes=p["duration_minutes"],
        ))

    needs_review = False
    seen_students = {}
    for m in matches:
        sid = m["student_id"]
        if m["status"] == "auto" and sid:
            if sid in seen_students:
                prev_m, prev_att = seen_students[sid]
                if m["confidence"] > prev_m["confidence"]:
                    prev_att.total_minutes = m["participant"]["total_minutes"]
                    prev_att.match_confidence = m["confidence"]
                    prev_att.match_method = m["method"]
                    seen_students[sid] = (m, prev_att)
            else:
                att = Attendance(
                    session_id=session.id, student_id=sid,
                    total_minutes=m["participant"]["total_minutes"],
                    match_confidence=m["confidence"], match_method=m["method"],
                    confirmed=True,
                )
                db.session.add(att)
                seen_students[sid] = (m, att)
        elif m["status"] in ("review", "unmatched"):
            needs_review = True
            if sid and sid not in seen_students:
                att = Attendance(
                    session_id=session.id, student_id=sid,
                    total_minutes=m["participant"]["total_minutes"],
                    match_confidence=m["confidence"], match_method=m["method"],
                    confirmed=False,
                )
                db.session.add(att)
                seen_students[sid] = (m, att)

    db.session.commit()
    return session, needs_review, {
        "reason": "imported", "participants": len(participants),
        "matched": matched_count, "people": len(consolidated),
    }


def run_autosync(days=7):
    """Import every recent meeting that belongs to a course. Returns a JSON-able summary."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    summary = {"days": days, "courses": [], "imported": 0, "needs_review": 0, "errors": 0,
               "headers_backfilled": backfill_session_headers()}
    courses = Course.query.filter(Course.zoom_meeting_id.isnot(None)).all()
    instances_by_room = {}
    meeting_cache = {}

    for course in courses:
        entry = {"course_id": course.id, "course": course.name, "room": course.zoom_meeting_id,
                 "imported": [], "skipped": 0, "errors": []}
        if Student.query.filter_by(course_id=course.id).count() == 0:
            entry["note"] = "no roster yet"
            summary["courses"].append(entry)
            continue
        try:
            if course.zoom_meeting_id not in instances_by_room:
                instances_by_room[course.zoom_meeting_id] = zoom_api.list_past_meeting_instances(course.zoom_meeting_id)
            instances = instances_by_room[course.zoom_meeting_id]
        except Exception as e:
            entry["errors"].append(f"list instances: {e}")
            summary["errors"] += 1
            summary["courses"].append(entry)
            continue

        for inst in instances:
            start = inst.get("start_time")
            if not start or start < since:
                continue
            uuid = inst["uuid"]
            if Session.query.filter_by(course_id=course.id, meeting_uuid=uuid).first():
                continue
            try:
                if uuid not in meeting_cache:
                    meeting_cache[uuid] = zoom_api.get_meeting_participants(uuid)
                sess, review, info = import_meeting(course, uuid, meeting_data=meeting_cache[uuid], require_fit=True)
            except Exception as e:
                db.session.rollback()
                entry["errors"].append(f"{uuid}: {e}")
                summary["errors"] += 1
                continue
            if sess is None:
                entry["skipped"] += 1
                continue
            entry["imported"].append({
                "session_id": sess.id, "date": str(sess.session_date), "topic": sess.zoom_topic,
                "participants": info["participants"], "matched": info["matched"], "needs_review": review,
            })
            summary["imported"] += 1
            if review:
                summary["needs_review"] += 1
        summary["courses"].append(entry)
    return summary
