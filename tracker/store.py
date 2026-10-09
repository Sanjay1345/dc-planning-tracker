"""Master CSV, caches, and the change log written on each run."""

import csv
import json
from datetime import date

from . import config

COLUMNS = [
    "id", "country", "authority", "reference", "received_date", "decided_date",
    "status", "decision", "stage", "is_data_centre", "category", "mw_it", "mw_grid",
    "floor_area_m2", "halls", "owner_parent", "confidence", "owner_method",
    "applicant", "agent", "company_number", "ownership_chain", "title_numbers", "landowner", "lender",
    "operator_mentioned",
    "description", "address", "lat", "lng", "url", "extract_notes", "source",
    "first_seen", "last_seen", "extract_hash", "project", "evidence",
]
RENAMED = {"owner_confidence": "confidence", "ch_company_number": "company_number"}
WATCHED = ["status", "decision", "stage", "decided_date"]
# Filled by extraction, owner lookup or hand review rather than by the source.
# Carried over from the master row when a fresh fetch leaves them blank, and
# always kept for hand-reviewed rows (extract_hash "manual...").
DERIVED = [
    "is_data_centre", "category", "stage", "mw_it", "mw_grid", "floor_area_m2", "halls",
    "operator_mentioned", "extract_notes", "extract_hash", "project",
    "owner_parent", "confidence", "owner_method", "company_number", "ownership_chain",
    "title_numbers", "landowner", "lender", "evidence",
]
OWNER_FIELDS = DERIVED[-9:]


def load_master(path=None):
    path = path or config.MASTER_CSV
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        rows = [{RENAMED.get(k, k): v for k, v in r.items()} for r in csv.DictReader(fh)]
    return {r["id"]: r for r in rows}


def save_master(rows, path=None):
    path = path or config.MASTER_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows.values(), key=lambda r: r.get("received_date") or "", reverse=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in ordered:
            w.writerow({c: "" if r.get(c) is None else r.get(c) for c in COLUMNS})


def load_cache(name):
    p = config.DATA_DIR / f"cache_{name}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def save_cache(name, data):
    (config.DATA_DIR / f"cache_{name}.json").write_text(json.dumps(data, indent=1, sort_keys=True))


def is_dc(row):
    v = str(row.get("is_data_centre", "")).lower()
    return v in ("true", "", "none")  # keep unclassified rows visible until Claude has seen them


def merge(master, fresh, today=None):
    """Merge fresh rows into master; return (master, new_ids, changes)."""
    today = today or date.today().isoformat()
    new_ids, changes = [], []
    for row in fresh:
        row = {k: ("" if v is None else str(v)) for k, v in row.items()}
        old = master.get(row["id"])
        if old is None:
            row["first_seen"] = today
            new_ids.append(row["id"])
        else:
            row["first_seen"] = old.get("first_seen") or today
            manual = old.get("extract_hash", "").startswith("manual")
            evidenced = bool(old.get("evidence"))  # ownership built from documents/registers
            for f in DERIVED:
                if manual and f not in OWNER_FIELDS and old.get(f):
                    row[f] = old[f]
                elif evidenced and f in OWNER_FIELDS:
                    row[f] = old.get(f, "")
                elif not row.get(f) and old.get(f):
                    row[f] = old[f]
            diffs = [(f, old.get(f, ""), row.get(f, "")) for f in WATCHED if old.get(f, "") != row.get(f, "")]
            if diffs:
                changes.append((row["id"], diffs))
        row["last_seen"] = today
        master[row["id"]] = row
    return master, new_ids, changes


def _line(r):
    mw = r.get("mw_it") or r.get("mw_grid")
    bits = [
        f"**{r.get('authority')}** ({r.get('country')})",
        f"{mw} MW" if mw else None,
        r.get("category") or None,
        f"owner: {r.get('owner_parent')} [{r.get('confidence')}]" if r.get("owner_parent") else
        (f"applicant: {r.get('applicant')}" if r.get("applicant") else None),
        f"stage: {r.get('stage') or r.get('status')}",
    ]
    desc = (r.get("description") or "")[:220]
    link = f" [link]({r['url']})" if r.get("url") else ""
    return "- " + " · ".join(b for b in bits if b) + f"\n  {desc}{link}"


def write_changes(master, new_ids, changes, today=None):
    today = today or date.today().isoformat()
    new = [master[i] for i in new_ids if is_dc(master[i])]
    moved = [(master[i], d) for i, d in changes if is_dc(master[i])]
    lines = [f"# Data centre planning changes {today}", "",
             f"{len(new)} new applications, {len(moved)} status changes.", ""]
    if new:
        lines += ["## New", ""] + [_line(r) for r in new] + [""]
    if moved:
        lines += ["## Status changes", ""]
        for r, diffs in moved:
            lines.append(_line(r))
            lines += [f"  - {f}: {a or '(blank)'} -> {b or '(blank)'}" for f, a, b in diffs]
        lines.append("")
    config.CHANGES_DIR.mkdir(parents=True, exist_ok=True)
    path = config.CHANGES_DIR / f"{today}.md"
    text = "\n".join(lines)
    if path.exists():  # second run on the same day: keep the earlier entries
        text = path.read_text(encoding="utf-8") + "\n---\n\n" + text
    path.write_text(text, encoding="utf-8")
    return path, len(new), len(moved)
