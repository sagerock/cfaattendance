"""Zoom Server-to-Server OAuth API client for pulling meeting reports."""

import os
import time
import requests
from datetime import datetime, date

ZOOM_ACCOUNT_ID = os.environ.get("ZOOM_ACCOUNT_ID", "")
ZOOM_CLIENT_ID = os.environ.get("ZOOM_CLIENT_ID", "")
ZOOM_CLIENT_SECRET = os.environ.get("ZOOM_CLIENT_SECRET", "")

_token_cache = {"token": None, "expires_at": 0}


def _get_access_token():
    """Get a Server-to-Server OAuth access token, cached until expiry."""
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    resp = requests.post(
        "https://zoom.us/oauth/token",
        params={"grant_type": "account_credentials", "account_id": ZOOM_ACCOUNT_ID},
        auth=(ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
    return _token_cache["token"]


def _api_get(path, params=None):
    """Make an authenticated GET request to the Zoom API."""
    token = _get_access_token()
    resp = requests.get(
        f"https://api.zoom.us/v2{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def is_configured():
    """Check if Zoom API credentials are set."""
    return bool(ZOOM_ACCOUNT_ID and ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET)


def list_past_meeting_instances(meeting_id):
    """List past instances of a recurring meeting.

    Returns list of dicts with keys: uuid, start_time (datetime), duration (minutes).
    Sorted newest first.
    """
    data = _api_get(f"/past_meetings/{meeting_id}/instances")
    instances = []
    for m in data.get("meetings", []):
        start = m.get("start_time", "")
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            start_dt = None
        instances.append({
            "uuid": m.get("uuid", ""),
            "start_time": start_dt,
        })
    instances.sort(key=lambda x: x["start_time"] or datetime.min, reverse=True)
    return instances


def get_meeting_participants(meeting_uuid):
    """Get participant report for a specific meeting instance.

    The UUID must be double-encoded if it contains / or //.

    Returns dict with:
        topic: str
        start_time: datetime
        duration_minutes: int
        participants: list of {raw_name, email, duration_minutes}
    """
    # Double-encode UUIDs containing / or //
    encoded_uuid = meeting_uuid
    if "/" in meeting_uuid:
        import urllib.parse
        encoded_uuid = urllib.parse.quote(urllib.parse.quote(meeting_uuid, safe=""), safe="")

    # Paginate through all participants
    all_participants = []
    next_page_token = ""
    while True:
        params = {"page_size": 300}
        if next_page_token:
            params["next_page_token"] = next_page_token

        data = _api_get(f"/report/meetings/{encoded_uuid}/participants", params)

        for p in data.get("participants", []):
            name = p.get("name", "").strip()
            if not name:
                continue
            all_participants.append({
                "raw_name": name,
                "email": p.get("user_email", "").strip() or None,
                "duration_minutes": p.get("duration", 0) // 60 if p.get("duration") else 0,
            })

        next_page_token = data.get("next_page_token", "")
        if not next_page_token:
            break

    # Parse meeting-level info
    topic = data.get("topic", "") if data else ""
    duration = data.get("total_minutes", 0) if data else 0

    start_time = None
    start_str = data.get("start_time", "") if data else ""
    if start_str:
        try:
            start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    return {
        "topic": topic,
        "start_time": start_time,
        "session_date": start_time.date() if start_time else None,
        "duration_minutes": duration,
        "participants": all_participants,
    }


def get_meeting_details(meeting_uuid):
    """Get basic details for a past meeting instance (topic, participant count, duration)."""
    encoded_uuid = meeting_uuid
    if "/" in meeting_uuid:
        import urllib.parse
        encoded_uuid = urllib.parse.quote(urllib.parse.quote(meeting_uuid, safe=""), safe="")

    data = _api_get(f"/report/meetings/{encoded_uuid}")
    start_time = None
    start_str = data.get("start_time", "")
    if start_str:
        try:
            start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    return {
        "uuid": meeting_uuid,
        "topic": data.get("topic", ""),
        "start_time": start_time,
        "duration_minutes": data.get("total_minutes", 0),
        "participant_count": data.get("participants_count", 0),
    }
