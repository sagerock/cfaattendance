"""Parse Thinkific user export CSV for student roster."""

import csv
import io
import re


def parse_roster_csv(file_content, course_filter=None):
    """Parse a Thinkific CSV export.

    Args:
        file_content: CSV file content as string
        course_filter: optional string to filter by Enrollments - list

    Returns:
        list of {name, email, phone}
    """
    reader = csv.DictReader(io.StringIO(file_content))
    students = []
    seen = set()

    for row in reader:
        first = (row.get("First Name") or "").strip()
        last = (row.get("Last Name") or "").strip()
        email = (row.get("Email") or "").strip()
        phone = (row.get("Phone Number") or "").strip()
        enrollments = row.get("Enrollments - list", "")

        if course_filter and course_filter.lower() not in enrollments.lower():
            continue

        name = f"{first} {last}".strip()
        if not name:
            continue

        # Normalize phone: strip non-digits
        phone_digits = re.sub(r"\D", "", phone) if phone else ""

        # Deduplicate by email (seat managers appear multiple times with different emails)
        key = email.lower() if email else name.lower()
        if key in seen:
            continue
        seen.add(key)

        students.append({
            "name": name,
            "email": email,
            "phone": phone_digits if phone_digits else None,
        })

    return students
