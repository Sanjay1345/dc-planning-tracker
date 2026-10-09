"""Collect ownership evidence for the top projects from public council portals.

    python tools/collect_evidence.py TOP60.json PLANIT_URLS.json OUT.json CACHE_DIR

Writes, per project and record: applicant/agent from the details page, the
document list, and every company number, title number and agreement party found
in the selected documents, each with the document URL and a quote.
"""

import csv
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import evidence  # noqa: E402
import portal  # noqa: E402

LEGAL = re.compile(r"106|heads of terms|unilateral|legal agreement|\bdeed\b|planning obligation", re.I)
USEFUL = re.compile(r"cover(ing)? letter|application form|^letter|certificate|ownership|planning statement|"
                    r"design (and|&) access|non[- ]technical summary|\bnts\b|introduction|letter from agent|"
                    r"supporting letter|statement of community", re.I)
SKIP = re.compile(r"objection|representation|comment|consultee|neighbour|public|site notice|photo|drawing|"
                  r"plan\b|elevation|section\b|figure|appendix [a-z]? ?- ?(?:figure|plan)", re.I)
MAIN_STAGES = {"approved", "outline application", "full application", "reserved matters", "amendment/variation",
               "approved subject to legal agreement", "appeal", "refused", "withdrawn",
               "lawful use / LDO certificate"}
IDOX_PATH = "/online-applications/applicationDetails.do"


EXTRA = {}  # project -> [portal URL of a parent application not in the tracker], filled from extra_targets.json


def choose_records(project, rows, urls):
    extra = [({"id": "PARENT:" + u, "stage": "approved", "received_date": ""}, {"url": u})
             for u in EXTRA.get(project["project"], [])]
    recs = [rows[i] for i in project["ids"]]
    main = [r for r in recs if r["stage"] in MAIN_STAGES]
    cond = sorted([r for r in recs if r["stage"] == "conditions discharge"], key=lambda r: r["received_date"],
                  reverse=True)[:2]
    chosen = main + cond or recs[:2]
    return extra + [(r, urls.get(r["id"], {})) for r in chosen]


def pick_docs(docs):
    legal = [d for d in docs if LEGAL.search(d["type"] + " " + d["description"])][:4]
    useful = [d for d in docs if d not in legal and USEFUL.search(d["type"] + " " + d["description"])
              and not SKIP.search(d["type"])][:5]
    return [(d, 15) for d in legal] + [(d, 3) for d in useful]


def run(top_path, urls_path, out_path, cache, only=None):
    only = set(only.split(",")) if only else None
    extra_path = Path(top_path).parent / "extra_targets.json"
    if extra_path.exists():
        EXTRA.update(json.load(open(extra_path)))
    top = json.load(open(top_path))
    urls = json.load(open(urls_path))
    rows = {r["id"]: r for r in csv.DictReader(open(Path(__file__).parent.parent / "data/applications.csv"))}
    f = portal.Fetcher(cache)
    pdf_dir = Path(cache) / "pdf"
    pdf_dir.mkdir(exist_ok=True)
    out = json.load(open(out_path)) if Path(out_path).exists() else {}
    for project in top:
        name = project["project"]
        res = out.setdefault(name, {"records": {}})
        for row, u in choose_records(project, rows, urls):
            url = u.get("url") or ""
            h = portal.handler_for(url)
            if row["id"] in res["records"] or not h or (only and h[0] not in only):
                continue
            _, get_details, get_docs = h
            rec = res["records"].setdefault(row["id"], {"portal_url": url, "items": [], "docs_checked": []})
            try:
                details, durl = get_details(f, url)
                rec["details_url"] = durl
                rec["details"] = {k: v for k, v in details.items()
                                  if re.search(r"applicant|agent|company|owner", k, re.I)}
                docs, docs_url = get_docs(f, url)
                rec["documents_url"], rec["n_documents"] = docs_url, len(docs)
                rec["legal_docs_listed"] = [d for d in docs if LEGAL.search(d["type"] + " " + d["description"])]
                for d, ocr_pages in pick_docs(docs):
                    path = pdf_dir / (re.sub(r"[^\w.-]", "_", urllib.parse.unquote(d["url"].split("/")[-1]))[-120:])
                    if not path.exists():
                        data = portal.download(f, d, docs_url)
                        if not data.startswith(b"%PDF"):
                            rec["docs_checked"].append({**d, "result": "not a PDF (unavailable or other format)"})
                            continue
                        path.write_bytes(data)
                    text, how = evidence.pdf_text(path, ocr_pages)
                    found = evidence.extract(text)
                    rec["docs_checked"].append({**d, "result": how, "found": {k: len(v) for k, v in found.items()}})
                    for kind, items in found.items():
                        for it in items:
                            rec["items"].append({"kind": kind, **it, "doc_url": d["url"], "doc": d["description"] or d["type"]})
            except portal.Refused as e:
                rec["error"] = f"refused: {e}"
            except Exception as e:  # keep going; record why
                rec["error"] = f"{type(e).__name__}: {e}"[:300]
            json.dump(out, open(out_path, "w"), indent=1)
            print(time.strftime("%H:%M:%S"), name[:50], row["id"], rec.get("error", ""),
                  len(rec["items"]), "items", flush=True)
    json.dump(out, open(out_path, "w"), indent=1)
    print("refused hosts:", f.refused)


if __name__ == "__main__":
    run(*sys.argv[1:6])
