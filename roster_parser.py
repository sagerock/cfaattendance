"""Parse CSV roster files for student roster.

Flexibly handles many CSV formats:
  - With or without a header row
  - Columns in any order
  - Name as one column ("Full Name") or two ("First Name" / "Last Name")
  - Email and phone detected by pattern when headers are absent or unrecognized
"""

import csv
import io
import re

# ---------------------------------------------------------------------------
# Pattern helpers
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE_RE = re.compile(r"[\d\s\-().+]{7,}")

# Header aliases (lowercased) → canonical field
_HEADER_MAP = {
    # Email
    "email": "email",
    "e-mail": "email",
    "email address": "email",
    "e-mail address": "email",
    "student email": "email",
    # First name
    "first name": "first",
    "first": "first",
    "firstname": "first",
    "given name": "first",
    # Last name
    "last name": "last",
    "last": "last",
    "lastname": "last",
    "surname": "last",
    "family name": "last",
    # Full name
    "name": "name",
    "full name": "name",
    "student name": "name",
    "student": "name",
    # Phone
    "phone": "phone",
    "phone number": "phone",
    "telephone": "phone",
    "mobile": "phone",
    "cell": "phone",
    "cell phone": "phone",
    # Thinkific enrollment filter
    "enrollments - list": "enrollments",
    "enrollments": "enrollments",
}


def _looks_like_email(value):
    return bool(_EMAIL_RE.fullmatch(value.strip()))


def _looks_like_phone(value):
    digits = re.sub(r"\D", "", value)
    return 7 <= len(digits) <= 15 and bool(_PHONE_RE.search(value))


def _clean_phone(value):
    digits = re.sub(r"\D", "", value)
    return digits if 7 <= len(digits) <= 15 else ""


def _map_headers(raw_headers):
    """Map raw CSV headers to canonical field names. Returns dict {col_index: field}."""
    mapping = {}
    for i, h in enumerate(raw_headers):
        key = h.strip().lower()
        if key in _HEADER_MAP:
            mapping[i] = _HEADER_MAP[key]
    return mapping


def _has_recognizable_headers(raw_headers):
    """Return True if at least one header maps to a known field."""
    mapping = _map_headers(raw_headers)
    return bool(mapping)


def _extract_from_mapped_row(row_values, col_map):
    """Given a row (list of values) and a col_map {index: field}, extract student dict."""
    fields = {}
    for i, val in enumerate(row_values):
        field = col_map.get(i)
        if field:
            fields[field] = val.strip()

    # Build name
    if fields.get("name"):
        name = fields["name"]
    elif fields.get("first") or fields.get("last"):
        name = f"{fields.get('first', '')} {fields.get('last', '')}".strip()
    else:
        name = ""

    return {
        "name": name,
        "email": fields.get("email", ""),
        "phone": _clean_phone(fields.get("phone", "")),
        "enrollments": fields.get("enrollments", ""),
    }


def _detect_columns(rows):
    """Auto-detect column roles by inspecting data patterns across all rows.
    Returns {col_index: field} for email, phone, and name candidates."""
    if not rows:
        return {}

    num_cols = max(len(r) for r in rows)
    col_email_score = [0] * num_cols
    col_phone_score = [0] * num_cols

    for row in rows:
        for i, val in enumerate(row):
            v = val.strip()
            if _looks_like_email(v):
                col_email_score[i] += 1
            if _looks_like_phone(v):
                col_phone_score[i] += 1

    mapping = {}

    # Pick the column with the most email-like values
    best_email = max(range(num_cols), key=lambda i: col_email_score[i])
    if col_email_score[best_email] > 0:
        mapping[best_email] = "email"

    # Pick the best phone column (excluding email col)
    phone_candidates = [i for i in range(num_cols) if i not in mapping]
    if phone_candidates:
        best_phone = max(phone_candidates, key=lambda i: col_phone_score[i])
        if col_phone_score[best_phone] > 0:
            mapping[best_phone] = "phone"

    # Name: assume the columns immediately before the email column are first/last
    if "email" in mapping.values():
        email_idx = best_email
        if email_idx >= 2:
            mapping[email_idx - 2] = "first"
            mapping[email_idx - 1] = "last"
        elif email_idx == 1:
            mapping[0] = "name"

    return mapping


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_roster_csv(file_content, course_filter=None):
    """Parse a roster CSV file.

    Flexibly handles CSVs with or without headers, columns in any order,
    and various header naming conventions.

    Args:
        file_content: CSV file content as string
        course_filter: optional string to filter by enrollment column (Thinkific)

    Returns:
        list of {name, email, phone}
    """
    all_rows = list(csv.reader(io.StringIO(file_content)))
    if not all_rows:
        return []

    first_row = all_rows[0]

    if _has_recognizable_headers(first_row):
        col_map = _map_headers(first_row)
        data_rows = all_rows[1:]
    else:
        col_map = _detect_columns(all_rows)
        data_rows = all_rows

    students = []
    seen = set()

    for row in data_rows:
        info = _extract_from_mapped_row(row, col_map)

        if not info["name"]:
            continue

        if course_filter and course_filter.lower() not in info["enrollments"].lower():
            continue

        key = info["email"].lower() if info["email"] else info["name"].lower()
        if key in seen:
            continue
        seen.add(key)

        students.append({
            "name": info["name"],
            "email": info["email"],
            "phone": info["phone"] if info["phone"] else None,
        })

    return students
