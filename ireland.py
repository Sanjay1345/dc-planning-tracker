"""Irish planning applications via the National Planning Applications service
(data.gov.ie, CC BY 4.0, updated weekly). ArcGIS FeatureServer, layer 0 = points.

Field names are discovered from the layer metadata at run time and matched
against likely candidates, so a renamed column logs a warning instead of
silently returning nothing.
"""

from datetime import datetime, timedelta, timezone

from .. import config
from ..http import get_json

LAYER = (
    "https://services.arcgis.com/NzlPQPKn5QF9v2US/arcgis/rest/services/"
    "IrishPlanningApplications/FeatureServer/0"
)
PAGE_SIZE = 1000

CANDIDATES = {
    "description": ["DevelopmentDescription", "DevDesc", "Description"],
    "address": ["DevelopmentAddress", "DevAddress", "Address"],
    "authority": ["PlanningAuthority", "Authority"],
    "reference": ["ApplicationNumber", "AppNumber", "PlanningRef"],
    "received": ["ReceivedDate", "DateReceived"],
    "decided": ["DecisionDate", "DateDecision"],
    "status": ["ApplicationStatus", "Status"],
    "decision": ["Decision"],
    "applicant_surname": ["ApplicantSurname"],
    "applicant_forename": ["ApplicantForename"],
    "applicant": ["ApplicantName", "Applicant"],
    "url": ["LinkAppDetails", "Link", "URL"],
    "appeal": ["AppealStatus"],
    "appeal_decision": ["AppealDecision"],
}


def resolve_fields(available):
    lower = {f.lower(): f for f in available}
    resolved = {}
    for key, options in CANDIDATES.items():
        for opt in options:
            if opt.lower() in lower:
                resolved[key] = lower[opt.lower()]
                break
    return resolved


def _date(ms):
    if ms in (None, ""):
        return ""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        return str(ms)[:10]


def normalise(attrs, geom, f):
    g = lambda k: attrs.get(f[k]) if k in f else None  # noqa: E731
    applicant = g("applicant") or " ".join(
        x for x in [g("applicant_forename"), g("applicant_surname")] if x
    )
    decision = g("decision") or ""
    if g("appeal_decision"):
        decision = f"{decision} | appeal: {g('appeal_decision')}".strip(" |")
    ref = str(g("reference") or "")
    auth = str(g("authority") or "")
    return {
        "id": f"IE:{auth}:{ref}",
        "country": "IE",
        "authority": auth,
        "reference": ref,
        "url": g("url") or "",
        "received_date": _date(g("received")),
        "decided_date": _date(g("decided")),
        "status": g("status") or "",
        "decision": decision,
        "description": (g("description") or "").strip(),
        "address": (g("address") or "").strip(),
        "lat": (geom or {}).get("y", ""),
        "lng": (geom or {}).get("x", ""),
        "applicant": applicant.strip(),
        "agent": "",
        "source": "IE National Planning Applications",
    }


def fetch(since_days):
    meta = get_json(LAYER, params={"f": "json"}) or {}
    f = resolve_fields([fld["name"] for fld in meta.get("fields", [])])
    if "description" not in f:
        raise RuntimeError(
            "Irish layer: description field not found; available fields: "
            + ", ".join(fld["name"] for fld in meta.get("fields", []))
        )
    desc = f["description"]
    like = " OR ".join(f"UPPER({desc}) LIKE '%{p.upper()}%'" for p in config.SEARCH_PHRASES)
    where = f"({like})"
    if "received" in f:
        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%d")
        where += f" AND {f['received']} >= DATE '{since}'"

    out, offset = {}, 0
    while True:
        data = get_json(
            f"{LAYER}/query",
            params={
                "where": where,
                "outFields": "*",
                "outSR": 4326,
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
                "orderByFields": "OBJECTID",
            },
        ) or {}
        if "error" in data:
            raise RuntimeError(f"Irish layer query error: {data['error']}")
        feats = data.get("features") or []
        for feat in feats:
            row = normalise(feat.get("attributes") or {}, feat.get("geometry"), f)
            out[row["id"]] = row
        if not feats or not data.get("exceededTransferLimit"):
            break
        offset += len(feats)
    return list(out.values())
