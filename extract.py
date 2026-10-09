"""Claude reads each free-text application and returns structured fields.

Rules given to the model: only report numbers stated in the text, never infer
MW from floor area. Results are cached on a hash of the description, so an
application is only sent again if its text changes.
"""

import hashlib
import json
import re

from . import config

FIELDS = [
    "is_data_centre", "category", "stage", "mw_it", "mw_grid", "floor_area_m2",
    "halls", "operator_mentioned", "extract_notes",
]

PROMPT = """You classify UK and Irish planning applications for an equity research team tracking data centre development.

Return ONLY a JSON object with these keys:
- is_data_centre: true if the application is for a data centre building/campus, or infrastructure built specifically to serve one (e.g. its substation, energy centre, generators). false for a server room inside another building, telecoms masts, or applications that merely mention data.
- category: one of "hyperscale/campus", "colocation", "AI/GPU", "enterprise", "edge/small", "ancillary to data centre", "not data centre".
- stage: one of "EIA screening/scoping", "outline application", "full application", "reserved matters", "amendment/variation", "conditions discharge", "approved", "refused", "withdrawn", "appeal", "unknown". Use the status/decision fields if given.
- mw_it: IT load in MW if the text states it, else null.
- mw_grid: grid/connection/total power in MW if stated, else null.
- floor_area_m2: gross floor area in square metres if stated (convert sq ft x 0.0929), else null.
- halls: number of data halls or buildings if stated, else null.
- operator_mentioned: any company/operator/brand named in the text, else null.
- extract_notes: one short sentence on anything material (phasing, on-site generation, battery, heat reuse).

Never estimate a number that is not written in the text.

Application:
"""


def _key(row):
    return hashlib.sha1((row["description"] + row.get("decision", "")).encode()).hexdigest()[:16]


def _parse(text):
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return {k: data.get(k) for k in FIELDS}


def _client():
    import anthropic

    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def extract(rows, cache, client=None):
    """Fill extraction fields on each row. `cache` maps hash -> fields and is updated in place."""
    if not config.ANTHROPIC_API_KEY and client is None:
        print("ANTHROPIC_API_KEY not set: skipping extraction")
        return rows
    client = client or _client()
    for row in rows:
        k = _key(row)
        if k not in cache:
            payload = {
                "description": row["description"],
                "address": row["address"],
                "applicant": row["applicant"],
                "status": row["status"],
                "decision": row["decision"],
            }
            msg = client.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=400,
                messages=[{"role": "user", "content": PROMPT + json.dumps(payload)}],
            )
            cache[k] = _parse(msg.content[0].text)
        row.update({f: cache[k].get(f) for f in FIELDS})
        row["extract_hash"] = k
    return rows
