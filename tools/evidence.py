"""Pull exact identifiers out of planning documents.

Only identifiers written in the document are returned, each with the quoted
sentence it came from: company registration numbers, HM Land Registry title
numbers, and the parties to legal agreements.
"""

import re
import subprocess
import tempfile
from pathlib import Path

# UK company numbers: 8 digits, or a 2-letter prefix + 6 digits (SC, NI, OC, SO, NC, FC, ...)
CO_NUM = r"((?:SC|NI|OC|SO|NC|FC|NF|GE|LP|SL|SE|R0|IP|SP|RC|ZC)?\d{6,8})"
COMPANY_PATTERNS = [
    re.compile(r"(?:company|registration|registered|reg\.?)\s*(?:no\.?|number|num\.?)\s*[:.]?\s*" + CO_NUM, re.I),
    re.compile(r"registered in (?:England|Scotland|Wales|England and Wales|England & Wales|Northern Ireland)"
               r"[^.\n]{0,40}?(?:no\.?|number)\s*[:.]?\s*" + CO_NUM, re.I),
    re.compile(r"\(\s*(?:company|registered)\s*(?:no\.?|number)\s*" + CO_NUM + r"\s*\)", re.I),
]
# Title numbers: 1-3 letters + 1-7 digits (e.g. ND116185, AGL123456, MX12345), found near "title".
TITLE_TOKEN = r"\b([A-Z]{1,3}\d{2,7})\b"
TITLE_CONTEXT = re.compile(r"(?i:title)\s*(?i:numbers|number|nos\.?|no\.?)?\s*[:.]?\s*"
                           r"((?:[A-Z]{1,3}\d{2,7}(?:\s|[,;&/]|\band\b)*){1,30})")
PARTY = re.compile(
    r"([A-Z][A-Z0-9&.,'()\- ]{2,90}?(?:LIMITED|LTD|PLC|LLP|L\.?P\.?|S\.?A\.? ?R\.?L\.?|B\.?V\.?|INC\.?|LLC|GMBH|COUNCIL))"
    r"[\s,(]{1,6}(?:a company |incorporated |registered |company )[^.;]{0,160}?" + CO_NUM
    + r"[^.;]{0,200}?(?:registered office[^.;]{0,150})?", re.S)
COUNTRIES = ["England and Wales", "England", "Scotland", "Wales", "Jersey", "Guernsey", "Luxembourg",
             "Delaware", "Ireland", "Netherlands", "Cayman", "British Virgin Islands", "Isle of Man", "United States"]


def pdf_text(path, ocr_pages=15):
    """Text layer if present; otherwise OCR the first `ocr_pages` pages."""
    out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True).stdout
    pages = int((re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(path)], capture_output=True,
                                                              text=True).stdout) or [0, 0])[1])
    if len(out.strip()) > 200 * max(1, min(pages, 3)):
        return out, "text layer"
    if not ocr_pages:
        return out, "no text layer"
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", "200", "-gray", "-f", "1", "-l", str(ocr_pages), str(path), f"{tmp}/p"],
                       capture_output=True)
        txt = []
        for img in sorted(Path(tmp).glob("p-*")):
            txt.append(subprocess.run(["tesseract", str(img), "-"], capture_output=True, text=True).stdout)
    return "\n".join(txt), f"OCR pages 1-{min(pages, ocr_pages)}"


def _quote(text, start, end, pad=160):
    return re.sub(r"\s+", " ", text[max(0, start - pad):end + pad]).strip()


def _valid_company(num):
    digits = re.sub(r"\D", "", num)
    return len(num) == 8 and digits and int(digits) > 0


def extract(text):
    """Return {'companies': [...], 'titles': [...], 'parties': [...]} each with a quote."""
    norm = re.sub(r"[ \t]+", " ", text)
    flat = re.sub(r"\s+", " ", norm)
    companies, seen = [], set()
    for pat in COMPANY_PATTERNS:
        for m in pat.finditer(flat):
            num = m.group(1).upper().zfill(8) if m.group(1).isdigit() else m.group(1).upper()
            if _valid_company(num) and num not in seen:
                seen.add(num)
                companies.append({"company_number": num, "quote": _quote(flat, m.start(), m.end())})
    titles, tseen = [], set()
    for m in TITLE_CONTEXT.finditer(flat):
        for tok in re.findall(TITLE_TOKEN, m.group(1)):
            if not re.match(r"^(?:SC|NI|OC)\d{6}$", tok) and tok not in tseen:
                tseen.add(tok)
                titles.append({"title_number": tok, "quote": _quote(flat, m.start(), m.end(), 120)})
    parties = []
    for m in PARTY.finditer(flat):
        name = re.sub(r"^(?:\(?\d+\)?\.?\s*|AND\s+|BETWEEN\s+)+", "", m.group(1).strip(" ,(")).strip()
        num = m.group(2).upper()
        num = num.zfill(8) if num.isdigit() else num
        ro = re.search(r"registered office (?:is )?(?:at|is) (.{5,160}?)(?:\(|;|\. |\s(?:and|AND)\s|$)",
                       flat[m.start():m.end() + 300], re.I)
        country = next((c for c in COUNTRIES if c.lower() in m.group(0).lower()), "")
        parties.append({"name": name, "company_number": num, "registered_office": ro.group(1).strip() if ro else "",
                        "jurisdiction": country, "quote": _quote(flat, m.start(), m.end(), 20)})
    return {"companies": companies, "titles": titles, "parties": parties}
