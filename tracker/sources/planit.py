"""UK planning applications via PlanIt (https://www.planit.org.uk/api/).

PlanIt aggregates ~400 UK council planning portals. We run one keyword search
per phrase, page through results, and normalise each record to the common
schema used across sources.

Check PlanIt's terms before commercial use, and set TRACKER_USER_AGENT to
something that identifies you (blank or generic agents can be blocked).
"""

from datetime import date, timedelta

from .. import config
from ..http import get_json

API = "https://www.planit.org.uk/api/applics/json"
PAGE_SIZE = 200  # PlanIt rejects responses over 1 MB; 500 records is ~1.3 MB
# PlanIt withholds most names and puts a placeholder instead; treat as blank so
# it is never sent to Companies House (which fuzzy-matches it to a real company).
PLACEHOLDERS = {"see source", "n/a", "na", "none", "unknown", "not available", "-"}


def _field(rec, key):
    """PlanIt puts some fields top-level and others under 'other_fields'."""
    val = rec.get(key)
    if val in (None, ""):
        val = (rec.get("other_fields") or {}).get(key)
    if isinstance(val, str) and val.strip().lower() in PLACEHOLDERS:
        return None
    return val


def normalise(rec):
    name = rec.get("name") or rec.get("uid")
    return {
        "id": f"UK:{name}",
        "country": "UK",
        "authority": rec.get("area_name") or "",
        "reference": rec.get("uid") or "",
        "url": rec.get("link") or rec.get("url") or "",
        "received_date": rec.get("start_date") or "",
        "decided_date": rec.get("decided_date") or "",
        "status": rec.get("app_state") or rec.get("status") or "",
        "decision": _field(rec, "decision") or "",
        "description": (rec.get("description") or "").strip(),
        "address": (rec.get("address") or "").strip(),
        "lat": rec.get("lat") or "",
        "lng": rec.get("lng") or "",
        "applicant": _field(rec, "applicant_company") or _field(rec, "applicant_name") or "",
        "agent": _field(rec, "agent_company") or _field(rec, "agent_name") or "",
        "source": "PlanIt",
    }


def fetch(since_days):
    start = (date.today() - timedelta(days=since_days)).isoformat()
    out = {}
    for phrase in config.SEARCH_PHRASES:
        page = 1  # PlanIt pages are 1-based; page=0 is a 400
        while True:
            data = get_json(
                API,
                params={
                    "search": f'"{phrase}"',
                    "start_date": start,
                    "pg_sz": PAGE_SIZE,
                    "page": page,
                    "sort": "-start_date",
                },
            )
            records = (data or {}).get("records") or []
            for rec in records:
                row = normalise(rec)
                out[row["id"]] = row
            total = (data or {}).get("total") or 0
            if not records or page * PAGE_SIZE >= total:
                break
            page += 1
    return list(out.values())
