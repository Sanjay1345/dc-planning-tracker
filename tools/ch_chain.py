"""Companies House: PSC chain to the top, plus charges, for exact company numbers.

Each step records the PSC entry it came from, so the chain is auditable. The walk
stops (with a stated reason) at a non-UK entity, an individual, a statement of
no registrable person, a missing registration number, or a loop.
"""
import json
import os
import re
import sys
import time

import requests

API = "https://api.company-information.service.gov.uk"
KEY = os.environ["COMPANIES_HOUSE_API_KEY"]
S = requests.Session()
S.auth = (KEY, "")
UK = ("ENGLAND", "WALES", "SCOTLAND", "UNITED KINGDOM", "NORTHERN IRELAND", "COMPANIES HOUSE", "UK", "GREAT BRITAIN")


def ch(path):
    for _ in range(3):
        r = S.get(API + path, timeout=60)
        time.sleep(0.6)  # well under 600 requests / 5 minutes
        if r.status_code == 429:
            time.sleep(60)
            continue
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    return None


def norm(num):
    num = re.sub(r"\s", "", num or "").upper()
    return num.zfill(8) if num.isdigit() else num


def chain(number, max_depth=8):
    steps, seen, num = [], set(), norm(number)
    for _ in range(max_depth):
        prof = ch(f"/company/{num}")
        if not prof:
            steps.append({"company_number": num, "stop": "not found at Companies House"})
            break
        node = {"company_number": num, "name": prof.get("company_name"), "status": prof.get("company_status"),
                "registered_office": ", ".join(v for k, v in (prof.get("registered_office_address") or {}).items()
                                               if k in ("address_line_1", "address_line_2", "locality", "postal_code") and v),
                "url": f"https://find-and-update.company-information.service.gov.uk/company/{num}"}
        steps.append(node)
        seen.add(num)
        pscs = (ch(f"/company/{num}/persons-with-significant-control") or {}).get("items") or []
        active = [p for p in pscs if not p.get("ceased_on")]
        corp = [p for p in active if "corporate-entity" in (p.get("kind") or "") or "legal-person" in (p.get("kind") or "")]
        if not corp:
            if any("individual" in (p.get("kind") or "") for p in active):
                node["stop"] = "controlled by individual(s): " + "; ".join(p.get("name", "") for p in active if "individual" in p.get("kind", ""))
            else:
                st = (ch(f"/company/{num}/persons-with-significant-control-statements") or {}).get("items") or []
                node["stop"] = "no corporate PSC" + (f" (statement: {st[0].get('statement')})" if st else "")
            break
        top = sorted(corp, key=lambda p: p.get("notified_on") or "")[-1]
        ident = top.get("identification") or {}
        reg = norm(ident.get("registration_number"))
        country = (ident.get("country_registered") or ident.get("place_registered") or ident.get("legal_authority") or "")
        node["psc"] = {"name": top.get("name"), "registration_number": reg, "country": country,
                       "natures_of_control": top.get("natures_of_control"), "notified_on": top.get("notified_on"),
                       "all_corporate_pscs": [p.get("name") for p in corp]}
        uk = any(c in country.upper() for c in UK)
        if not uk:
            steps.append({"name": top.get("name"), "registration_number": reg, "country": country,
                          "stop": "parent registered outside the UK (Companies House cannot see further)"})
            break
        if not reg:
            steps.append({"name": top.get("name"), "stop": "UK parent has no registration number in PSC entry"})
            break
        if reg in seen:
            node["stop"] = "loop in PSC chain"
            break
        num = reg
    return steps


def charges(number):
    data = ch(f"/company/{norm(number)}/charges") or {}
    out = []
    for c in data.get("items") or []:
        desc = (c.get("particulars") or {}).get("description") or ""
        out.append({"status": c.get("status"), "created_on": c.get("created_on"),
                    "lenders": [p.get("name") for p in c.get("persons_entitled") or []],
                    "description": desc[:500],
                    "title_numbers": sorted(set(re.findall(r"\b[A-Z]{1,3}\d{2,7}\b", desc))),
                    "url": f"https://find-and-update.company-information.service.gov.uk{(c.get('links') or {}).get('self', '')}"})
    return out


if __name__ == "__main__":
    nums = json.load(open(sys.argv[1]))
    out_path = sys.argv[2]
    out = json.load(open(out_path)) if os.path.exists(out_path) else {}
    for n in nums:
        n = norm(n)
        if n in out:
            continue
        out[n] = {"chain": chain(n), "charges": charges(n)}
        json.dump(out, open(out_path, "w"), indent=1)
        last = out[n]["chain"][-1]
        print(n, "->", " > ".join(s.get("name") or s.get("company_number", "?") for s in out[n]["chain"]),
              "|", last.get("stop", ""), "|", len(out[n]["charges"]), "charges", flush=True)
