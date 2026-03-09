"""Name cleaning, consolidation, and fuzzy matching for Zoom→Roster matching."""

import re
from rapidfuzz import fuzz


# Words to ignore inside parenthetical content (device names, pronouns)
DEVICE_WORDS = {"iphone", "ipad", "android", "galaxy", "pixel", "macbook", "laptop", "phone"}
PRONOUN_PATTERNS = re.compile(
    r"^(she|her|hers|he|him|his|they|them|theirs|ze|hir)([/,\s]|$)", re.IGNORECASE
)


def clean_name(raw_name):
    """Clean a Zoom display name, extracting useful aliases.

    Returns:
        (cleaned_name, list_of_aliases)
    """
    name = raw_name.strip()
    aliases = []

    # Extract parenthetical content
    paren_matches = re.findall(r"\(([^)]+)\)", name)
    for content in paren_matches:
        content_lower = content.strip().lower()
        # Skip device names
        if any(dw in content_lower for dw in DEVICE_WORDS):
            continue
        # Skip pure pronouns
        if PRONOUN_PATTERNS.match(content_lower):
            continue
        # Skip if it's just a number (phone in parens)
        if re.match(r"^\d+$", content.strip()):
            continue
        aliases.append(content.strip())

    # Remove parenthetical content from main name
    name = re.sub(r"\([^)]*\)", "", name).strip()

    # Filter out device-like main names (e.g. "Samantha's iPhone")
    if any(dw in name.lower() for dw in DEVICE_WORDS):
        # Try to extract real name portion before device word
        parts = re.split(r"[''`]s?\s+", name, maxsplit=1)
        if parts and not any(dw in parts[0].lower() for dw in DEVICE_WORDS):
            aliases.append(parts[0].strip())
        name = parts[0].strip() if parts else name

    # Remove emojis
    name = re.sub(
        r"[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0000FE00-\U0000FE0F\U0000200D\u2728\u2764\u2601-\u26FF]+",
        "", name
    ).strip()

    # Remove trailing pipes and URLs/social handles
    name = re.sub(r"\|.*$", "", name).strip()

    # Clean up stray parentheses/brackets left over from partial extraction
    name = re.sub(r"[()[\]]", "", name).strip()

    # Remove trailing org/location markers like "-Texas", "-California"
    # but keep hyphenated last names like "Alsoraimi-Espiritu"
    # We'll keep the full name as-is for matching purposes

    # Normalize whitespace
    name = re.sub(r"\s+", " ", name).strip()

    return name, aliases


def is_phone_number(name):
    """Check if a cleaned name is a phone number."""
    digits = re.sub(r"\D", "", name)
    return len(digits) >= 10 and len(digits) <= 15


def is_username(name):
    """Check if a cleaned name looks like a username rather than a real name."""
    # Single word with underscores or all lowercase no spaces
    if "_" in name:
        return True
    # Single lowercase word that doesn't look like a name
    if " " not in name and name == name.lower() and len(name) > 3:
        return True
    return False


def consolidate_participants(participants):
    """Group and merge Zoom participants that are the same person.

    Takes raw participant dicts and returns consolidated entries with summed durations.
    Each entry: {cleaned_name, aliases, total_minutes, raw_names, email}
    """
    # Build name→group mapping
    groups = {}  # canonical key → group dict
    name_to_key = {}  # cleaned name or alias → canonical key

    for p in participants:
        cleaned, aliases = clean_name(p["raw_name"])
        cleaned_lower = cleaned.lower()
        all_names = [cleaned_lower] + [a.lower() for a in aliases]

        # Find existing group this participant belongs to
        found_key = None
        for n in all_names:
            if n in name_to_key:
                found_key = name_to_key[n]
                break

        if found_key:
            # Add to existing group
            group = groups[found_key]
            group["total_minutes"] += p["duration_minutes"]
            group["raw_names"].append(p["raw_name"])
            if p.get("email") and not group.get("email"):
                group["email"] = p["email"]
            # Register new name variants
            for n in all_names:
                if n not in name_to_key:
                    name_to_key[n] = found_key
                    group["aliases"].add(n)
        else:
            # Create new group
            key = cleaned_lower
            groups[key] = {
                "cleaned_name": cleaned,
                "aliases": set(all_names),
                "total_minutes": p["duration_minutes"],
                "raw_names": [p["raw_name"]],
                "email": p.get("email"),
            }
            for n in all_names:
                name_to_key[n] = key

    # Convert sets to lists for JSON serialization
    result = []
    for g in groups.values():
        result.append({
            "cleaned_name": g["cleaned_name"],
            "aliases": list(g["aliases"]),
            "total_minutes": g["total_minutes"],
            "raw_names": g["raw_names"],
            "email": g["email"],
        })
    return result


def match_participants_to_roster(consolidated, students, aliases_db, course_id):
    """Match consolidated Zoom participants to roster students.

    Args:
        consolidated: list from consolidate_participants()
        students: list of Student model objects
        aliases_db: list of Alias model objects for this course
        course_id: int

    Returns:
        list of match dicts:
            {participant, student_id, student_name, confidence, method, status}
            status: "auto", "review", "unmatched"
    """
    # Build lookup structures
    alias_map = {}  # lowercase alias → student
    for a in aliases_db:
        alias_map[a.alias_name.lower()] = a.student

    student_by_name = {}  # lowercase name → student
    student_by_email = {}  # lowercase email → student
    student_by_phone = {}  # digits → student
    for s in students:
        student_by_name[s.name.lower()] = s
        if s.email:
            student_by_email[s.email.lower()] = s
        if s.phone:
            phone_digits = re.sub(r"\D", "", s.phone)
            if phone_digits:
                student_by_phone[phone_digits] = s
                # Also index without country code (strip leading 1 for US)
                if phone_digits.startswith("1") and len(phone_digits) == 11:
                    student_by_phone[phone_digits[1:]] = s

    results = []

    for p in consolidated:
        match = _match_single(p, student_by_name, student_by_email, student_by_phone, alias_map, students)
        match["participant"] = p
        results.append(match)

    return results


def _match_single(participant, by_name, by_email, by_phone, alias_map, all_students):
    """Try to match a single consolidated participant."""
    cleaned = participant["cleaned_name"]
    cleaned_lower = cleaned.lower()
    aliases = participant["aliases"]

    # 1. Alias lookup (exact)
    for name_variant in [cleaned_lower] + [a.lower() for a in aliases if a != cleaned_lower]:
        if name_variant in alias_map:
            s = alias_map[name_variant]
            return {"student_id": s.id, "student_name": s.name,
                    "confidence": 1.0, "method": "alias", "status": "auto"}

    # 2. Exact match
    for name_variant in [cleaned_lower] + [a.lower() for a in aliases if a != cleaned_lower]:
        if name_variant in by_name:
            s = by_name[name_variant]
            return {"student_id": s.id, "student_name": s.name,
                    "confidence": 1.0, "method": "exact", "status": "auto"}

    # 3. Email match
    if participant.get("email"):
        email_lower = participant["email"].lower()
        if email_lower in by_email:
            s = by_email[email_lower]
            return {"student_id": s.id, "student_name": s.name,
                    "confidence": 1.0, "method": "email", "status": "auto"}

    # 4. Phone match
    if is_phone_number(cleaned):
        digits = re.sub(r"\D", "", cleaned)
        if digits in by_phone:
            s = by_phone[digits]
            return {"student_id": s.id, "student_name": s.name,
                    "confidence": 1.0, "method": "phone", "status": "auto"}
        # Try without leading 1
        if digits.startswith("1") and len(digits) == 11:
            short = digits[1:]
            if short in by_phone:
                s = by_phone[short]
                return {"student_id": s.id, "student_name": s.name,
                        "confidence": 1.0, "method": "phone", "status": "auto"}

    # 5. Fuzzy match
    best_score = 0
    best_student = None
    for s in all_students:
        # Try all name variants against student name
        for name_variant in [cleaned] + [a for a in aliases if a.lower() != cleaned_lower]:
            score = max(
                fuzz.token_sort_ratio(name_variant.lower(), s.name.lower()),
                fuzz.partial_ratio(name_variant.lower(), s.name.lower()),
            )
            if score > best_score:
                best_score = score
                best_student = s

    if best_student and best_score >= 85:
        return {"student_id": best_student.id, "student_name": best_student.name,
                "confidence": best_score / 100.0, "method": "fuzzy", "status": "auto"}
    elif best_student and best_score >= 60:
        return {"student_id": best_student.id, "student_name": best_student.name,
                "confidence": best_score / 100.0, "method": "fuzzy", "status": "review"}
    else:
        return {"student_id": None, "student_name": None,
                "confidence": best_score / 100.0 if best_student else 0,
                "method": "none", "status": "unmatched"}
