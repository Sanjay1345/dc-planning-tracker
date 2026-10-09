"""Turn collected evidence into ownership columns on data/applications.csv.

Inputs (scratch files, not committed): portal evidence JSONs, Agile IE applicants,
Land Registry address hits + the LR SQLite index, and Companies House chains.

Confidence rules (as agreed):
  high        an exact identifier links the site to the company: a company number
              quoted in a legal agreement/document for the site, or a title number
              quoted in a document that HM Land Registry registers to that company.
  medium      Land Registry title found by site address whose proprietor is
              corroborated (also named as applicant/party, or a data-centre company);
              the site-to-title link is by address, so check the title plan.
  low         name only (applicant name on the portal with no number).
  unverified  memory / press - never written as owner.
"""
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DC_WORDS = re.compile(r"DATA ?CENT|\bDC\b|\bDCS?\d|HYPERSCALE|DIGITAL|\bCOLO|CLOUD|COMPUTE|MSFT|MCIO|VANTAGE|VIRTUS|"
                      r"\bARK\b|EQUINIX|GOOGLE|AMAZON|MICROSOFT|CYRUSONE|IRON MOUNTAIN|NTT|YONDR|KAO|PURE DC|STACK|"
                      r"GLOBAL SWITCH|SEGRO|HDCI|EDGECONNEX|NSCALE", re.I)


def load_json(p, default):
    return json.load(open(p)) if Path(p).exists() else default


def main(scratch, lr_db):
    S = Path(scratch)
    top = json.load(open(S / "top60.json"))
    portal_ev = {}
    for fn in ("evidence_idox.json", "evidence_ocella.json", "evidence_arcus.json"):
        for proj, v in load_json(S / fn, {}).items():
            portal_ev.setdefault(proj, {}).update(v["records"])
    agile = load_json(S / "evidence_agile_ie.json", {})
    lr_hits = load_json(S / "lr_address_hits.json", {})
    chains = load_json(S / "ch_results.json", {})
    db = sqlite3.connect(lr_db)

    rows = list(csv.DictReader(open(ROOT / "data/applications.csv", encoding="utf-8")))
    by_project = {}
    for r in rows:
        by_project.setdefault(r["project"], []).append(r)

    report = []
    for proj in top:
        name = proj["project"]
        recs = portal_ev.get(name, {})
        ev, companies, titles, applicants, agents = [], {}, {}, set(), set()
        # 1. portal details: applicant / agent (name only)
        for rid, rec in recs.items():
            det = rec.get("details") or {}
            for k, v in det.items():
                if re.search(r"applicant", k, re.I) and v and not re.match(r"(?i)c/o|not available|see source", v):
                    applicants.add(v)
                    ev.append(f"{rec.get('details_url', rec.get('portal_url'))} | {k}: \"{v}\"")
                if re.search(r"agent", k, re.I) and "address" not in k.lower() and v and v != "Not Available":
                    agents.add(v)
        for rid, a in agile.items():
            if a["project"] == name and a["applicant"]:
                applicants.add(a["applicant"])
                ev.append(f"{a['api_url']} (public API behind {a['portal_url']}) | applicant: \"{a['applicant']}\"")
        # 2. document identifiers
        for rid, rec in recs.items():
            for it in rec.get("items", []):
                q = it["quote"][:300].replace('"', "'")
                if it["kind"] == "titles":
                    titles.setdefault(it["title_number"], f"{it['doc_url']} | \"{q}\"")
                elif it["kind"] in ("parties", "companies"):
                    num = it["company_number"]
                    role = "agreement party" if it["kind"] == "parties" or re.search(
                        r"(?i)deed|agreement|undertaking|between|owner|developer", q) else "document mention"
                    if any(a.split()[0].lower() in q.lower() for a in agents if a and len(a.split()[0]) > 3) and role != "agreement party":
                        role = "agent letterhead"
                    if num not in companies or (role == "agreement party" and companies[num]["role"] != "agreement party"):
                        companies[num] = {"role": role, "evidence": f"{it['doc_url']} | \"{q}\"",
                                          "name": it.get("name", "")}
        # 3. Land Registry: titles quoted in documents (exact), then address hits (corroborated only)
        landowners = {}
        for t, src in titles.items():
            for (tenure, addr, pname, pnum, country) in db.execute(
                    "select t.tenure,t.address,p.name,p.company_number,p.country from titles t join proprietors p "
                    "on p.title=t.title where t.title=?", (t,)):
                landowners.setdefault((pname, pnum), {"titles": set(), "basis": "high", "evidence": []})
                landowners[(pname, pnum)]["titles"].add(t)
                landowners[(pname, pnum)]["evidence"].append(
                    f"HM Land Registry CCOD/OCOD Oct 2026: title {t} ({tenure}, '{addr}') proprietor {pname} {pnum}; title cited at {src}")
        named = {re.sub(r"\W", "", n.upper()) for n in applicants} | {re.sub(r"\W", "", c.get("name", "").upper())
                                                                      for c in companies.values() if c.get("name")}
        for h in lr_hits.get(name, []):
            title, tenure, addr, pc, src, pname, pnum, country = h
            if (pname, pnum) in landowners:
                continue
            corroborated = (pnum and pnum in companies) or re.sub(r"\W", "", pname.upper()) in named or DC_WORDS.search(pname)
            if corroborated:
                lo = landowners.setdefault((pname, pnum), {"titles": set(), "basis": "medium", "evidence": []})
                lo["titles"].add(title)
                lo["evidence"].append(f"HM Land Registry CCOD/OCOD Oct 2026 address match: title {title} ('{addr}') "
                                      f"proprietor {pname} {pnum}")
        # 4. pick company numbers to chain: agreement parties + corroborated landowners
        chain_nums = [n for n, c in companies.items() if c["role"] == "agreement party"]
        chain_nums += [pnum for (pname, pnum), lo in landowners.items() if pnum and not pnum.startswith(("IP", "RS"))]
        report.append({"project": name, "applicants": sorted(applicants), "agents": sorted(agents),
                       "companies": companies, "titles": titles,
                       "landowners": {f"{k[0]}|{k[1]}": {**v, "titles": sorted(v["titles"])} for k, v in landowners.items()},
                       "chain_numbers": list(dict.fromkeys(chain_nums)), "portal_evidence": ev,
                       "records_checked": {rid: {"error": r.get("error", ""), "docs": len(r.get("docs_checked", [])),
                                                 "legal_listed": len(r.get("legal_docs_listed", []))}
                                           for rid, r in recs.items()}})
    json.dump(report, open(S / "ownership_report.json", "w"), indent=1, default=list)
    nums = sorted({n for p in report for n in p["chain_numbers"]})
    json.dump(nums, open(S / "ch_numbers.json", "w"))
    print(len(report), "projects;", len(nums), "company numbers to chain")


if __name__ == "__main__":
    main(*sys.argv[1:3])
