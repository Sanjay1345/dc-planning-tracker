"""Work out who ultimately owns the applicant.

1. Known names: regex patterns in data/known_owners.csv matched against the
   applicant, agent, description and any operator Claude found. Edit that file
   as you learn new special-purpose company names; it is the manual layer.
2. UK only: Companies House API. Find the applicant company, then walk up its
   corporate "persons with significant control" chain while the controller is
   itself a UK company. The top of that chain is the furthest Companies House
   can see (often a Jersey/Luxembourg/US holding company, named but not traced).

Ireland has no free equivalent of the PSC register, so Irish rows rely on step 1.
"""

import csv
import re

from . import config
from .http import get_json

CH_API = "https://api.company-information.service.gov.uk"
MAX_DEPTH = 6
SUFFIXES = r"\b(LIMITED|LTD|PLC|LLP|LP|UK|HOLDINGS?|GROUP|COMPANY|CO|THE)\b"


def clean(name):
    name = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper())
    name = re.sub(SUFFIXES, " ", name)
    return re.sub(r"\s+", " ", name).strip()


def load_known(path=None):
    path = path or config.KNOWN_OWNERS_CSV
    with open(path, newline="", encoding="utf-8") as fh:
        return [
            (re.compile(r["pattern"], re.I), r["parent"])
            for r in csv.DictReader(fh)
            if r.get("pattern") and not r["pattern"].startswith("#")
        ]


def match_known(texts, known):
    blob = " | ".join(t for t in texts if t)
    for pattern, parent in known:
        if pattern.search(blob):
            return parent
    return None


def _ch(path, params=None):
    return get_json(CH_API + path, params=params, auth=(config.COMPANIES_HOUSE_API_KEY, ""))


def find_company(name):
    """Return (company_number, title, exact) for the best Companies House match."""
    if not name.strip():
        return None
    res = _ch("/search/companies", {"q": name, "items_per_page": 10}) or {}
    target = clean(name)
    items = res.get("items") or []
    for it in items:
        if clean(it.get("title")) == target:
            return it["company_number"], it["title"], True
    if items:
        it = items[0]
        return it["company_number"], it["title"], False
    return None


def psc_chain(number, title):
    """Walk corporate PSCs upwards. Returns list of entity names, applicant first."""
    chain, seen = [title], {number}
    for _ in range(MAX_DEPTH):
        pscs = (_ch(f"/company/{number}/persons-with-significant-control") or {}).get("items") or []
        corporate = [
            p for p in pscs
            if "corporate-entity" in (p.get("kind") or "") and not p.get("ceased_on")
        ]
        if not corporate:
            break
        top = corporate[0]
        chain.append(top.get("name", "?"))
        ident = top.get("identification") or {}
        reg = (ident.get("registration_number") or "").strip().upper()
        country = (ident.get("country_registered") or ident.get("place_registered") or "").upper()
        uk = any(c in country for c in ("ENGLAND", "WALES", "SCOTLAND", "UNITED KINGDOM", "COMPANIES HOUSE"))
        if not reg or not uk or reg in seen:
            break
        seen.add(reg)
        number = reg.zfill(8) if reg.isdigit() else reg
    return chain


def resolve(rows, known, cache):
    """Fill owner fields. `cache` maps applicant name -> CH result to save API calls."""
    for row in rows:
        texts = [row.get("applicant"), row.get("agent"), row.get("description"), row.get("operator_mentioned")]
        parent = match_known(texts, known)
        row.update(owner_parent=parent or "", owner_method="known name" if parent else "",
                   owner_confidence="high" if parent else "", ch_company_number="", ownership_chain="")

        applicant = row.get("applicant") or ""
        if row["country"] != "UK" or not applicant or not config.COMPANIES_HOUSE_API_KEY:
            continue
        if applicant not in cache:
            found = find_company(applicant)
            cache[applicant] = (
                {"number": found[0], "exact": found[2], "chain": psc_chain(found[0], found[1])}
                if found else {}
            )
        hit = cache[applicant]
        if not hit:
            continue
        chain = hit["chain"]
        row["ch_company_number"] = hit["number"]
        row["ownership_chain"] = " > ".join(chain)
        if not parent:
            chain_parent = match_known(chain, known)
            row["owner_parent"] = chain_parent or chain[-1]
            row["owner_method"] = "Companies House chain" + (" + known name" if chain_parent else "")
            row["owner_confidence"] = "high" if hit["exact"] and chain_parent else (
                "medium" if hit["exact"] else "low (name match not exact)")
    return rows
