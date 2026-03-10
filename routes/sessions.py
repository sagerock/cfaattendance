from flask import Blueprint, render_template, request, redirect, url_for, flash
from extensions import db
from models import Course, Student, Session, ZoomParticipant, Attendance, Alias, SkippedParticipant
from zoom_parser import parse_zoom_csv
from matching import consolidate_participants, match_participants_to_roster
import zoom_api

sessions_bp = Blueprint("sessions", __name__)


@sessions_bp.route("/courses/<int:course_id>/sessions", methods=["POST"])
def upload_session(course_id):
    course = Course.query.get_or_404(course_id)
    file = request.files.get("zoom_file")
    if not file or not file.filename:
        flash("Please select a Zoom CSV file.", "error")
        return redirect(url_for("courses.course_detail", course_id=course_id))

    content = file.read().decode("utf-8-sig")
    parsed = parse_zoom_csv(content)

    if not parsed["participants"]:
        flash("No participants found in Zoom CSV.", "error")
        return redirect(url_for("courses.course_detail", course_id=course_id))

    label = request.form.get("label", "").strip() or parsed["topic"]

    # Create session
    session = Session(
        course_id=course.id,
        label=label,
        zoom_topic=parsed["topic"],
        session_date=parsed["session_date"],
        duration_minutes=parsed["duration_minutes"],
    )
    db.session.add(session)
    db.session.flush()  # get session.id

    # Store raw participants
    for p in parsed["participants"]:
        zp = ZoomParticipant(
            session_id=session.id,
            raw_name=p["raw_name"],
            email=p["email"],
            duration_minutes=p["duration_minutes"],
        )
        db.session.add(zp)

    # Consolidate and match
    consolidated = consolidate_participants(parsed["participants"])
    students = Student.query.filter_by(course_id=course.id).all()
    aliases = Alias.query.filter_by(course_id=course.id).all()
    matches = match_participants_to_roster(consolidated, students, aliases, course.id)

    # Save attendance records, deduplicating by student_id (keep highest confidence)
    needs_review = False
    seen_students = {}  # student_id -> (match, attendance_obj)
    for m in matches:
        sid = m["student_id"]
        if m["status"] == "auto" and sid:
            # Check for duplicate student assignment
            if sid in seen_students:
                prev_m, prev_att = seen_students[sid]
                if m["confidence"] > prev_m["confidence"]:
                    # Replace: update previous record
                    prev_att.total_minutes = m["participant"]["total_minutes"]
                    prev_att.match_confidence = m["confidence"]
                    prev_att.match_method = m["method"]
                    seen_students[sid] = (m, prev_att)
                # else keep previous, skip this one
            else:
                att = Attendance(
                    session_id=session.id,
                    student_id=sid,
                    total_minutes=m["participant"]["total_minutes"],
                    match_confidence=m["confidence"],
                    match_method=m["method"],
                    confirmed=True,
                )
                db.session.add(att)
                seen_students[sid] = (m, att)
        elif m["status"] in ("review", "unmatched"):
            needs_review = True
            if sid and sid not in seen_students:
                att = Attendance(
                    session_id=session.id,
                    student_id=sid,
                    total_minutes=m["participant"]["total_minutes"],
                    match_confidence=m["confidence"],
                    match_method=m["method"],
                    confirmed=False,
                )
                db.session.add(att)
                seen_students[sid] = (m, att)

    db.session.commit()

    if needs_review:
        flash("Session uploaded. Some matches need review.", "warning")
        return redirect(url_for("sessions.review_matches", session_id=session.id))
    else:
        flash("Session uploaded. All participants matched automatically!", "success")
        return redirect(url_for("courses.course_detail", course_id=course.id))


@sessions_bp.route("/sessions/<int:session_id>/review", methods=["GET", "POST"])
def review_matches(session_id):
    session = Session.query.get_or_404(session_id)
    course = session.course
    students = Student.query.filter_by(course_id=course.id).order_by(Student.name).all()

    if request.method == "POST":
        # Process auto-match changes (reassign or unmatch)
        auto_keys = [k for k in request.form if k.startswith("auto_match_")]
        for key in auto_keys:
            idx = key.replace("auto_match_", "")
            student_id_str = request.form.get(key)
            cleaned_name = request.form.get(f"auto_cleaned_name_{idx}", "")
            aliases_str = request.form.get(f"auto_aliases_{idx}", "")
            minutes = int(request.form.get(f"auto_minutes_{idx}", 0))
            raw_names = request.form.get(f"auto_raw_names_{idx}", cleaned_name)
            original_id = int(request.form.get(f"auto_original_{idx}", 0))

            if student_id_str == "skip":
                # Unmatch: delete attendance record, save as skipped
                if original_id:
                    Attendance.query.filter_by(
                        session_id=session.id, student_id=original_id
                    ).delete()
                existing_skip = SkippedParticipant.query.filter_by(
                    session_id=session.id, cleaned_name=cleaned_name
                ).first()
                if not existing_skip:
                    db.session.add(SkippedParticipant(
                        session_id=session.id,
                        cleaned_name=cleaned_name,
                        aliases=aliases_str,
                        total_minutes=minutes,
                        raw_names=raw_names,
                    ))
            elif int(student_id_str) != original_id:
                # Reassigned to different student
                new_student_id = int(student_id_str)
                if original_id:
                    Attendance.query.filter_by(
                        session_id=session.id, student_id=original_id
                    ).delete()
                existing = Attendance.query.filter_by(
                    session_id=session.id, student_id=new_student_id
                ).first()
                if existing:
                    existing.total_minutes = minutes
                    existing.match_method = "manual"
                    existing.match_confidence = 1.0
                    existing.confirmed = True
                else:
                    db.session.add(Attendance(
                        session_id=session.id,
                        student_id=new_student_id,
                        total_minutes=minutes,
                        match_confidence=1.0,
                        match_method="manual",
                        confirmed=True,
                    ))
                # Save aliases for new assignment
                student = Student.query.get(new_student_id)
                if student:
                    for alias_name in [cleaned_name] + [a.strip() for a in aliases_str.split("|") if a.strip()]:
                        alias_lower = alias_name.lower().strip()
                        if not alias_lower or alias_lower == student.name.lower():
                            continue
                        if not Alias.query.filter_by(course_id=course.id, alias_name=alias_lower).first():
                            db.session.add(Alias(course_id=course.id, student_id=student.id, alias_name=alias_lower))

        # Process review/unmatched/skipped form
        participant_keys = [k for k in request.form if k.startswith("match_")]

        for key in participant_keys:
            idx = key.replace("match_", "")
            student_id_str = request.form.get(key)
            minutes = int(request.form.get(f"minutes_{idx}", 0))
            cleaned_name = request.form.get(f"cleaned_name_{idx}", "")
            aliases_str = request.form.get(f"aliases_{idx}", "")

            if student_id_str == "skip":
                # Save as skipped so it shows up on re-review
                raw_names = request.form.get(f"raw_names_{idx}", cleaned_name)
                existing_skip = SkippedParticipant.query.filter_by(
                    session_id=session.id, cleaned_name=cleaned_name
                ).first()
                if not existing_skip:
                    db.session.add(SkippedParticipant(
                        session_id=session.id,
                        cleaned_name=cleaned_name,
                        aliases=aliases_str,
                        total_minutes=minutes,
                        raw_names=raw_names,
                    ))
                continue

            if student_id_str == "new":
                # Create new student from the Zoom name
                new_name = request.form.get(f"new_name_{idx}", cleaned_name).strip()
                if new_name:
                    existing = Student.query.filter_by(course_id=course.id, name=new_name).first()
                    if existing:
                        student_id = existing.id
                    else:
                        new_student = Student(course_id=course.id, name=new_name)
                        db.session.add(new_student)
                        db.session.flush()
                        student_id = new_student.id
                else:
                    continue
            else:
                student_id = int(student_id_str)

            # Update or create attendance
            att = Attendance.query.filter_by(
                session_id=session.id, student_id=student_id
            ).first()
            if att:
                att.total_minutes = minutes
                att.confirmed = True
                att.match_method = "manual"
                att.match_confidence = 1.0
            else:
                att = Attendance(
                    session_id=session.id,
                    student_id=student_id,
                    total_minutes=minutes,
                    match_confidence=1.0,
                    match_method="manual",
                    confirmed=True,
                )
                db.session.add(att)

            # Remove from skipped if previously skipped
            SkippedParticipant.query.filter_by(
                session_id=session.id, cleaned_name=cleaned_name
            ).delete()

            # Save aliases for future matching
            student = Student.query.get(student_id)
            if student:
                all_aliases = [cleaned_name]
                if aliases_str:
                    all_aliases.extend(a.strip() for a in aliases_str.split("|") if a.strip())
                for alias_name in all_aliases:
                    alias_lower = alias_name.lower().strip()
                    if not alias_lower or alias_lower == student.name.lower():
                        continue
                    existing_alias = Alias.query.filter_by(
                        course_id=course.id, alias_name=alias_lower
                    ).first()
                    if not existing_alias:
                        db.session.add(Alias(
                            course_id=course.id,
                            student_id=student.id,
                            alias_name=alias_lower,
                        ))

        # Handle manually added absent students
        manual_ids = request.form.getlist("manual_present")
        for sid_str in manual_ids:
            sid = int(sid_str)
            existing = Attendance.query.filter_by(session_id=session.id, student_id=sid).first()
            if existing:
                existing.confirmed = True
                existing.match_method = "manual"
                existing.match_confidence = 1.0
            else:
                db.session.add(Attendance(
                    session_id=session.id,
                    student_id=sid,
                    total_minutes=0,
                    match_confidence=1.0,
                    match_method="manual",
                    confirmed=True,
                ))

        # Also confirm any unconfirmed auto-matches
        Attendance.query.filter_by(session_id=session.id, confirmed=False).update(
            {"confirmed": True}
        )

        db.session.commit()
        flash("Matches confirmed and aliases saved.", "success")
        return redirect(url_for("courses.course_detail", course_id=course.id))

    # GET: build review data
    # Re-run matching to get full picture
    raw_participants = [
        {"raw_name": zp.raw_name, "email": zp.email, "duration_minutes": zp.duration_minutes}
        for zp in session.participants
    ]
    consolidated = consolidate_participants(raw_participants)
    aliases_db = Alias.query.filter_by(course_id=course.id).all()
    matches = match_participants_to_roster(consolidated, students, aliases_db, course.id)

    # Load previously skipped entries
    skipped = SkippedParticipant.query.filter_by(session_id=session.id).all()
    skipped_names = {s.cleaned_name.lower() for s in skipped}

    # Split into categories, filtering out already-skipped participants
    auto_matches = [m for m in matches if m["status"] == "auto"]
    review_matches_list = [
        m for m in matches if m["status"] == "review"
        and m["participant"]["cleaned_name"].lower() not in skipped_names
    ]
    unmatched = [
        m for m in matches if m["status"] == "unmatched"
        and m["participant"]["cleaned_name"].lower() not in skipped_names
    ]

    # Find absent students (on roster, no confirmed attendance, not auto-matched)
    confirmed_ids = {
        a.student_id for a in
        Attendance.query.filter_by(session_id=session.id, confirmed=True).all()
    }
    auto_matched_ids = {m["student_id"] for m in auto_matches if m["student_id"]}
    present_ids = confirmed_ids | auto_matched_ids
    absent_students = [s for s in students if s.id not in present_ids]

    return render_template(
        "review_matches.html",
        session=session,
        course=course,
        students=students,
        auto_matches=auto_matches,
        review_matches=review_matches_list,
        unmatched=unmatched,
        skipped=skipped,
        absent_students=absent_students,
    )


@sessions_bp.route("/courses/<int:course_id>/zoom-sync")
def zoom_sync(course_id):
    """List recent Zoom meetings for this course's room, ready to import."""
    course = Course.query.get_or_404(course_id)

    if not zoom_api.is_configured():
        flash("Zoom API credentials not configured. Set ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, and ZOOM_CLIENT_SECRET.", "error")
        return redirect(url_for("courses.course_detail", course_id=course_id))

    if not course.zoom_meeting_id:
        flash("No Zoom room linked to this course. Set the Zoom Meeting ID first.", "error")
        return redirect(url_for("courses.course_detail", course_id=course_id))

    try:
        instances = zoom_api.list_past_meeting_instances(course.zoom_meeting_id)
    except Exception as e:
        flash(f"Error fetching Zoom meetings: {e}", "error")
        return redirect(url_for("courses.course_detail", course_id=course_id))

    # Enrich each instance with participant count and details
    meetings = []
    for inst in instances[:20]:  # Last 20 meetings max
        try:
            details = zoom_api.get_meeting_details(inst["uuid"])
            details["start_time"] = inst["start_time"]
            meetings.append(details)
        except Exception:
            # If we can't get details for an instance, include basic info
            meetings.append({
                "uuid": inst["uuid"],
                "topic": "",
                "start_time": inst["start_time"],
                "duration_minutes": 0,
                "participant_count": 0,
            })

    return render_template("zoom_sync.html", course=course, meetings=meetings)


@sessions_bp.route("/courses/<int:course_id>/zoom-import", methods=["POST"])
def zoom_import(course_id):
    """Import a specific Zoom meeting instance as a session."""
    course = Course.query.get_or_404(course_id)
    meeting_uuid = request.form.get("meeting_uuid", "").strip()
    label = request.form.get("label", "").strip()

    if not meeting_uuid:
        flash("No meeting selected.", "error")
        return redirect(url_for("sessions.zoom_sync", course_id=course_id))

    try:
        meeting_data = zoom_api.get_meeting_participants(meeting_uuid)
    except Exception as e:
        flash(f"Error fetching participant data: {e}", "error")
        return redirect(url_for("sessions.zoom_sync", course_id=course_id))

    if not meeting_data["participants"]:
        flash("No participants found for this meeting.", "error")
        return redirect(url_for("sessions.zoom_sync", course_id=course_id))

    if not label:
        label = meeting_data.get("topic", "")

    # Create session (same logic as CSV upload)
    session = Session(
        course_id=course.id,
        label=label,
        zoom_topic=meeting_data.get("topic", ""),
        session_date=meeting_data.get("session_date"),
        duration_minutes=meeting_data.get("duration_minutes", 0),
    )
    db.session.add(session)
    db.session.flush()

    # Store raw participants
    for p in meeting_data["participants"]:
        zp = ZoomParticipant(
            session_id=session.id,
            raw_name=p["raw_name"],
            email=p["email"],
            duration_minutes=p["duration_minutes"],
        )
        db.session.add(zp)

    # Consolidate and match
    consolidated = consolidate_participants(meeting_data["participants"])
    students = Student.query.filter_by(course_id=course.id).all()
    aliases = Alias.query.filter_by(course_id=course.id).all()
    matches = match_participants_to_roster(consolidated, students, aliases, course.id)

    # Save attendance records
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
                    session_id=session.id,
                    student_id=sid,
                    total_minutes=m["participant"]["total_minutes"],
                    match_confidence=m["confidence"],
                    match_method=m["method"],
                    confirmed=True,
                )
                db.session.add(att)
                seen_students[sid] = (m, att)
        elif m["status"] in ("review", "unmatched"):
            needs_review = True
            if sid and sid not in seen_students:
                att = Attendance(
                    session_id=session.id,
                    student_id=sid,
                    total_minutes=m["participant"]["total_minutes"],
                    match_confidence=m["confidence"],
                    match_method=m["method"],
                    confirmed=False,
                )
                db.session.add(att)
                seen_students[sid] = (m, att)

    db.session.commit()

    if needs_review:
        flash(f"Imported {len(meeting_data['participants'])} participants from Zoom. Some matches need review.", "warning")
        return redirect(url_for("sessions.review_matches", session_id=session.id))
    else:
        flash(f"Imported {len(meeting_data['participants'])} participants from Zoom. All matched!", "success")
        return redirect(url_for("courses.course_detail", course_id=course.id))


@sessions_bp.route("/sessions/<int:session_id>/delete", methods=["POST"])
def delete_session(session_id):
    session = Session.query.get_or_404(session_id)
    course_id = session.course_id
    db.session.delete(session)
    db.session.commit()
    flash("Session deleted.", "success")
    return redirect(url_for("courses.course_detail", course_id=course_id))
