# cfaattendance — DEPRECATED 2026-05-09

**This standalone app has been superseded by the SageRock System.**

The Zoom + Thinkific + attendance-matching logic was ported into:

- `~/scripts/sagerock/system/tools/_industry/education/`

CfA staff now interact with attendance via email to **iris@ask.sagerock.com**
instead of the standalone web UI here.

## Status

- Railway service `cfa-attendance` is **paused** (deployment removed,
  project + env vars retained as a safety net).
- This repo is kept as historical reference. **Don't push new work here.**
- See `~/scripts/sagerock/system/CLAUDE.md` and
  `~/scripts/sagerock/system/docs/superpowers/specs/2026-05-09-iris-education-tools-day-4-design.md`
  for the new architecture.

## What was here

Flask app for CfA's Zoom attendance pipeline:
- Zoom Server-to-Server OAuth client (`zoom_api.py`)
- Roster CSV ingest (`roster_parser.py`)
- Name cleaning + fuzzy matching (`matching.py`)
- Web UI for course/session/report management + manual fuzzy-match review

The web UI for fuzzy-match review currently has no equivalent in
SageRock System. Pending matches are surfaced via admin-digest email
instead. A `manual_match` Iris tool or a small admin UI on
`system.sagerock.com` is planned for Day 5+.
