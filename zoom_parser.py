"""Parse Zoom meeting CSV exports (two-section format)."""

import csv
import io
from datetime import datetime


def parse_zoom_csv(file_content):
    """Parse a Zoom CSV with meeting metadata header and participant rows.

    Returns:
        dict with keys:
            topic: str
            duration_minutes: int
            session_date: date
            participants: list of {raw_name, email, duration_minutes}
    """
    reader = csv.reader(io.StringIO(file_content))
    rows = list(reader)

    # Section 1: Meeting metadata (rows 0-1)
    # Row 0: header — Topic, ID, Host, Duration (minutes), Start time, End time, Participants
    # Row 1: values
    meta = dict(zip(rows[0], rows[1]))
    topic = meta.get("Topic", "")
    duration = int(meta.get("Duration (minutes)", 0))

    session_date = None
    start_time = meta.get("Start time", "")
    if start_time:
        try:
            session_date = datetime.strptime(start_time.strip('"'), "%m/%d/%Y %I:%M:%S %p").date()
        except ValueError:
            pass

    # Find participant section (after blank row)
    participant_start = None
    for i, row in enumerate(rows):
        if not row or all(cell.strip() == "" for cell in row):
            participant_start = i + 1
            break

    if participant_start is None or participant_start >= len(rows):
        return {"topic": topic, "duration_minutes": duration, "session_date": session_date, "participants": []}

    # Row after blank is participant header
    p_header = rows[participant_start]
    participants = []
    for row in rows[participant_start + 1:]:
        if not row or all(cell.strip() == "" for cell in row):
            continue
        p = dict(zip(p_header, row))
        name = p.get("Name (original name)", "").strip()
        if not name:
            continue
        email = p.get("Email", "").strip()
        try:
            dur = int(p.get("Total duration (minutes)", 0))
        except (ValueError, TypeError):
            dur = 0
        participants.append({
            "raw_name": name,
            "email": email if email else None,
            "duration_minutes": dur,
        })

    return {
        "topic": topic,
        "duration_minutes": duration,
        "session_date": session_date,
        "participants": participants,
    }
