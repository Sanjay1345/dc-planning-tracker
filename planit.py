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
PAGE_SIZE = 500


def _field(rec, key):
    """PlanIt puts some fields top-level and others under 'other_fields'."""
    if rec.get(key) not in (None, ""):
        return rec.get(key)
    return (rec.get("other_fields") or {}).get(key)


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
        page = 0
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
            if not records or (page + 1) * PAGE_SIZE >= total:
                break
            page += 1
    return list(out.values())
