"""Settings for the tracker. Edit keywords here, not in the source modules."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MASTER_CSV = DATA_DIR / "applications.csv"
CHANGES_DIR = DATA_DIR / "changes"
KNOWN_OWNERS_CSV = DATA_DIR / "known_owners.csv"

# Phrases searched in application descriptions. Kept deliberately broad;
# the Claude step throws out false positives (e.g. a server room in an office).
SEARCH_PHRASES = [
    "data centre",
    "data center",
    "datacentre",
    "datacenter",
    "data hall",
    "data storage facility",
    "digital infrastructure campus",
    "hyperscale",
    "ai campus",
    "compute campus",
]

# How far back the first run looks, and the overlap each nightly run uses
# so nothing is missed if a run fails.
BACKFILL_DAYS = int(os.getenv("BACKFILL_DAYS", "730"))
NIGHTLY_LOOKBACK_DAYS = int(os.getenv("NIGHTLY_LOOKBACK_DAYS", "14"))

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
COMPANIES_HOUSE_API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY", "")

USER_AGENT = os.getenv(
    "TRACKER_USER_AGENT",
    "dc-planning-tracker/0.1 (research; contact: set TRACKER_USER_AGENT)",
)
REQUEST_PAUSE_SECS = float(os.getenv("REQUEST_PAUSE_SECS", "1.5"))
