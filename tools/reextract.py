"""Re-run identifier extraction on already-downloaded PDFs that have a text layer
(after extractor improvements), without fetching anything again.

    python tools/reextract.py EVIDENCE.json CACHE_DIR
"""
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import evidence  # noqa: E402
from collect_evidence import doc_filename  # noqa: E402

path, cache = sys.argv[1], Path(sys.argv[2]) / "pdf"
data = json.load(open(path))
n = 0
for proj in data.values():
    for rec in proj["records"].values():
        keep = [it for it in rec.get("items", [])]
        for d in rec.get("docs_checked", []):
            if d.get("result") != "text layer":
                continue
            f = cache / doc_filename(d["url"])
            legacy = cache / re.sub(r"[^\w.-]", "_", urllib.parse.unquote(d["url"].split("/")[-1]))[-120:]
            f = f if f.exists() else legacy
            if "#" in d["url"] or not f.exists():  # legacy names are only unique for plain URLs
                if not (cache / doc_filename(d["url"])).exists():
                    continue
            text = subprocess.run(["pdftotext", "-layout", str(f), "-"], capture_output=True, text=True).stdout
            keep = [it for it in keep if it["doc_url"] != d["url"]]
            for kind, items in evidence.extract(text).items():
                for it in items:
                    keep.append({"kind": kind, **it, "doc_url": d["url"], "doc": d["description"] or d["type"]})
            n += 1
        rec["items"] = keep
json.dump(data, open(path, "w"), indent=1)
print("re-extracted", n, "documents")
