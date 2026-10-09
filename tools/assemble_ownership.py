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
ORG = re.compile(r"(?i)\b(limited|ltd|plc|llp|lp|inc|llc|s\.?a\.? ?r\.?l|b\.?v|gmbh|corporation|company|council|"
                 r"group|holdings|trust|partnership|ireland|operations)\b")
DC_WORDS = re.compile(r"DATA ?CENT|DATACENT|\bDC\b|\bDCS?\d|HYPERSCALE|MSFT|MCIO|VANTAGE|VIRTUS|"
                      r"\bARK\b|EQUINIX|GOOGLE|AMAZON|MICROSOFT|CYRUSONE|IRON MOUNTAIN|NTT|YONDR|KAO|PURE DC|STACK|"
                      r"GLOBAL SWITCH|SEGRO|HDCI|EDGECONNEX|NSCALE", re.I)


LEGAL = re.compile(r"(?i)106|heads of terms|unilateral|legal.agreement|deed|planning obligation")
CONSULTANTS = re.compile(r"(?i)savills|tetra tech|quod|turley|lichfields|dp9|arup|\bwsp\b|aecom|stantec|\brps\b|"
                         r"pegasus|iceni|montagu evans|carter jonas|cbre|\bjll\b|knight frank|avison young|"
                         r"barton willmore|gerald eve|david lock|nexus|hgh|logan|union4|rapleys|newmark|freeths")
ROLE = re.compile(r"[(\[]\s*(?:together|each|jointly|both)?\s*(?:as\s+)?(?:the\s+)?[\"'\u201c\u2018]?\s*"
                  r"(?:the\s+)?([A-Z][A-Za-z \-]{2,30}?)\s*[\"'\u201d\u2019]\s*[)\]]")
LENDER = re.compile(r"(?i)mortgagee|chargee|lender|security (?:trustee|agent)")


FOOTER = re.compile(r"(?i)is a (?:company|limited company) registered in|registered number\s*:|registered in england no|"
                    r"a subsidiary of|vat (?:reg|no)")
NOT_OWNER = re.compile(r"(?i)council|consultant|contractor|beneficiary|surety|guarantor|architect|engineer|"
                       r"warrant|agent|county|authority|mortgagee|chargee|lender")
DEVELOPER = re.compile(r"(?i)developer|interested party|applicant|promoter|tenant|leasehold owner|purchaser")


def classify(item):
    """Role of a company number in a document: agreement party (with its defined role), lender, consultant, mention."""
    q, src = item["quote"], item.get("doc", "") + " " + item.get("doc_url", "")
    num = item["company_number"]
    key = num.lstrip("0")[:6]
    pos = q.find(key) if key and key in q else -1
    before = q[max(0, pos - 220):pos] if pos >= 0 else q
    after = q[pos:pos + 260] if pos >= 0 else q
    if FOOTER.search(before[-160:] + after[:80]) or (CONSULTANTS.search(before[-120:]) and not LENDER.search(after)):
        return "consultant/footer", ""
    m = ROLE.search(after)
    if LEGAL.search(src) and m and LENDER.search(m.group(1)):
        return "lender", m.group(1).strip()
    if LEGAL.search(src) and (item["kind"] == "parties" or re.search(r"(?i)registered office|incorporated", after)
                              or re.search(r"(?i)incorporated in|registered in", before[-120:])):
        m = ROLE.search(after)
        role = m.group(1).strip() if m else ""
        if LENDER.search(role):
            return "lender", role
        return "agreement party", role
    return "document mention", ""


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
                v = re.sub(r"(?i)^c/o\s+", "", v)
                if re.search(r"applicant", k, re.I) and v and ORG.search(v) and not re.search(r"(?i)council|not available|see source|^fao", v):
                    applicants.add(v)
                    ev.append(f"{rec.get('details_url', rec.get('portal_url'))} | {k}: \"{v}\"")
                if re.search(r"agent", k, re.I) and "address" not in k.lower() and v and v != "Not Available":
                    agents.add(v)
        for rid, a in agile.items():
            if a["project"] == name and a["applicant"] and ORG.search(a["applicant"]):
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
                    role, defined = classify(it)
                    rank = {"agreement party": 3, "lender": 3, "document mention": 1, "consultant/footer": 0}
                    if num not in companies or rank[role] > rank[companies[num]["role"]] or (
                            it.get("name") and not companies[num].get("name")):
                        companies[num] = {"role": role, "defined_as": defined or companies.get(num, {}).get("defined_as", ""),
                                          "evidence": f"{it['doc_url']} | \"{q}\"",
                                          "name": it.get("name", "") or companies.get(num, {}).get("name", "")}
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
            by_number = bool(pnum) and pnum in companies
            by_name = re.sub(r"\W", "", pname.upper()) in named or bool(DC_WORDS.search(pname))
            if by_number or by_name:
                basis = "medium" if by_number else "low"
                lo = landowners.setdefault((pname, pnum), {"titles": set(), "basis": basis, "evidence": []})
                lo["titles"].add(title)
                lo["evidence"].append(f"HM Land Registry CCOD/OCOD Oct 2026 address match: title {title} ('{addr}') "
                                      f"proprietor {pname} {pnum}")
        # 4. pick company numbers to chain: agreement parties + corroborated landowners
        chain_nums = [n for n, c in companies.items() if c["role"] in ("agreement party", "lender")]
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





RANK = {"high": 3, "medium": 2, "low": 1}


def chain_summary(num, chains):
    c = chains.get(num)
    if not c:
        return None
    steps = c["chain"]
    names = [f"{s.get('name') or '?'} ({s.get('company_number') or s.get('registration_number') or 'no number'}"
             f"{', ' + s['country'] if s.get('country') else ''})" for s in steps]
    last = steps[-1]
    stop = last.get("stop", "")
    top = last.get("name") or last.get("company_number")
    if stop.startswith("controlled by individual"):
        top_desc = f"{top} (individual PSC: {stop.split(': ', 1)[1]})"
    else:
        top_desc = top
    lenders = sorted({l for ch in c["charges"] if ch["status"] != "fully-satisfied" for l in ch["lenders"] if l})
    charge_titles = sorted({t for ch in c["charges"] for t in ch["title_numbers"]})
    ev = [f"{s['url']} | {s.get('name')} PSC: {s['psc']['name']} ({s['psc'].get('registration_number') or ''} "
          f"{s['psc'].get('country') or ''}) notified {s['psc'].get('notified_on')}" for s in steps if s.get("psc")]
    ev += [f"{ch['url']} | charge {ch['created_on']} ({ch['status']}) to {', '.join(ch['lenders'])}"
           + (f"; titles {', '.join(ch['title_numbers'])}" if ch["title_numbers"] else "") for ch in c["charges"][:5]]
    return {"chain": " > ".join(names), "top": top_desc, "stop": stop, "lenders": lenders,
            "charge_titles": charge_titles, "evidence": ev}


def write_csv(scratch):
    S = Path(scratch)
    report = json.load(open(S / "ownership_report.json"))
    chains = load_json(S / "ch_results.json", {})
    path = ROOT / "data/applications.csv"
    sys.path.insert(0, str(ROOT))
    from tracker import store
    master = store.load_master(path)
    summary = []
    for p in report:
        # candidates: (confidence, company number, name, basis text, evidence lines, titles)
        cands = []
        for num, c in p["companies"].items():
            role = c.get("defined_as") or ""
            q = c["evidence"].split(" | ", 1)[-1]
            if c["role"] != "agreement party" or NOT_OWNER.search(role) or (
                    not role and re.search(r"(?i)warrant|contractor|consultant|beneficiary", q)):
                continue
            ch_ok = chains.get(num, {}).get("chain", [{}])[0].get("stop") != "not found at Companies House"
            if not ch_ok and num.isdigit():
                continue  # e.g. a Jersey registration number; its OE ID is listed separately
            cands.append(("high", num, c.get("name", ""), f"named as {role or 'party'} in legal agreement",
                          [c["evidence"]], [], 2 if DEVELOPER.search(role) else 1 if "owner" in role.lower() else 0))
        for key, lo in p["landowners"].items():
            pname, pnum = key.split("|")
            cands.append((lo["basis"], pnum, pname, "registered proprietor (HM Land Registry)", lo["evidence"][:4],
                          lo["titles"], 1))
        lenders_doc = [f"{c.get('name') or (chains.get(num, {}).get('chain') or [{}])[0].get('name') or '?'} ({num}, "
                       f"{c.get('defined_as') or 'lender'} in legal agreement)"
                       for num, c in p["companies"].items() if c["role"] == "lender"]
        apps = p["applicants"]
        best = sorted(cands, key=lambda c: (RANK[c[0]], c[6], bool(chains.get(c[1]))), reverse=True)
        row_vals = {}
        if best:
            conf, num, name, basis, ev, _, _ = best[0]
            cs = chain_summary(num, chains) if num else None
            if not name and num in chains:
                name = chains[num]["chain"][0].get("name") or ""
            titles = sorted({t for c in cands if RANK[c[0]] >= 2 for t in c[5]} | set(p["titles"]))
            low_titles = sorted({t for c in cands if c[0] == "low" for t in c[5]} - set(titles))
            landowners = sorted({f"{c[2]} ({c[1] or 'no number'}) [{c[0]}]" for c in cands if "proprietor" in c[3]})
            lenders = sorted(set(lenders_doc) | set(cs["lenders"] if cs else []))
            evidence = [f"[{conf}] {basis}: {e}" for e in ev] + (cs["evidence"] if cs else [])
            evidence += [f"[{c[0]}] {c[3]} {c[2]} ({c[1]}): {c[4][0]}" for c in best[1:6]]
            evidence += [f"[low] portal applicant (name only): {e}" for e in p["portal_evidence"][:2]]
            row_vals = {
                "company_number": num, "confidence": conf + ("" if conf == "high" else
                                                              " (address match; check title plan)" if "proprietor" in basis
                                                              else ""),
                "owner_parent": (cs["top"] if cs else name), "ownership_chain": cs["chain"] if cs else name,
                "owner_method": "evidence chain: " + basis + ("; Companies House PSC chain" if cs else ""),
                "title_numbers": "; ".join(titles + [t + " (low)" for t in low_titles][:10]),
                "landowner": "; ".join(landowners[:6]), "lender": "; ".join(lenders),
                "evidence": " || ".join(evidence)[:6000]}
            summary.append({"project": p["project"], "confidence": conf, "company": f"{name} ({num})",
                            "parent": cs["top"] if cs else name, "chain": cs["chain"] if cs else name,
                            "stop": cs["stop"] if cs else "no Companies House chain (no UK number)",
                            "titles": titles, "lenders": lenders, "applicants": apps})
        elif apps:
            row_vals = {"company_number": "", "confidence": "low (name only)", "owner_parent": apps[0],
                        "ownership_chain": apps[0], "owner_method": "portal applicant name (no company number)",
                        "title_numbers": "", "landowner": "", "lender": "",
                        "evidence": " || ".join(f"[low] {e}" for e in p["portal_evidence"][:3])}
            summary.append({"project": p["project"], "confidence": "low", "company": "", "parent": apps[0],
                            "chain": "", "stop": "applicant name only; no company number found", "titles": [],
                            "lenders": [], "applicants": apps})
        else:
            errs = sorted({r["error"].split(":")[1].strip() if ":" in r["error"] else r["error"]
                           for r in p["records_checked"].values() if r["error"]})
            summary.append({"project": p["project"], "confidence": "", "company": "", "parent": "", "chain": "",
                            "stop": ("portal unreachable: " + ", ".join(errs)) if errs else
                            "no identifier in documents checked; no corroborated Land Registry title",
                            "titles": [], "lenders": [], "applicants": []})
        if row_vals:
            for r in master.values():
                if r.get("project") == p["project"]:
                    r.update(row_vals)
    store.save_master(master, path)
    json.dump(summary, open(S / "ownership_summary.json", "w"), indent=1)
    print("written;", sum(1 for s in summary if s["confidence"] == "high"), "high,",
          sum(1 for s in summary if s["confidence"] == "medium"), "medium,",
          sum(1 for s in summary if s["confidence"] == "low"), "low,",
          sum(1 for s in summary if not s["confidence"]), "none")


if __name__ == "__main__":
    if sys.argv[1] == "write":
        write_csv(sys.argv[2])
    else:
        main(*sys.argv[1:3])
