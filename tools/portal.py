"""Polite fetching of public planning-portal pages and documents.

One request per host every PAUSE seconds, a browser-like session (cookies kept,
as a normal visitor's would be), public pages only, and no retry: a host that
refuses (403/406/429, connection reset) is recorded and skipped for the rest of
the run.
"""

import hashlib
import html
import re
import time
import urllib.parse
from pathlib import Path

import requests

PAUSE = 4.0
UA = "Mozilla/5.0 (X11; Linux x86_64) dc-planning-tracker research (contact: repository owner)"
REFUSAL_CODES = {401, 403, 406, 429}


class Refused(Exception):
    pass


class Fetcher:
    def __init__(self, cache_dir):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.sessions, self.last, self.refused = {}, {}, {}

    def _session(self, host):
        if host not in self.sessions:
            s = requests.Session()
            s.headers["User-Agent"] = UA
            self.sessions[host] = s
        return self.sessions[host]

    def get(self, url, referer=None, binary=False, cache=True):
        host = urllib.parse.urlparse(url).netloc
        if host in self.refused:
            raise Refused(f"{host}: {self.refused[host]}")
        key = self.cache / hashlib.sha1(url.encode()).hexdigest()
        if cache and key.exists():
            data = key.read_bytes()
            return data if binary else data.decode("utf-8", "replace")
        wait = self.last.get(host, 0) + PAUSE - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            r = self._session(host).get(url, timeout=120, headers={"Referer": referer} if referer else None)
        except requests.RequestException as e:
            self.refused[host] = f"connection failed: {type(e).__name__}"
            raise Refused(f"{host}: {self.refused[host]}") from None
        finally:
            self.last[host] = time.time()
        if r.status_code in REFUSAL_CODES:
            self.refused[host] = f"HTTP {r.status_code}"
            raise Refused(f"{host}: HTTP {r.status_code}")
        r.raise_for_status()
        if cache:
            key.write_bytes(r.content)
        return r.content if binary else r.text


def post(f, url, data, referer=None, headers=None):
    """Polite POST (used only for public disclaimer/acceptance forms)."""
    host = urllib.parse.urlparse(url).netloc
    if host in f.refused:
        raise Refused(f"{host}: {f.refused[host]}")
    wait = f.last.get(host, 0) + PAUSE - time.time()
    if wait > 0:
        time.sleep(wait)
    try:
        h = {"Referer": referer} if referer else {}
        h.update(headers or {})
        r = f._session(host).post(url, data=data, timeout=300, headers=h)
    except requests.RequestException as e:
        f.refused[host] = f"connection failed: {type(e).__name__}"
        raise Refused(f"{host}: {f.refused[host]}") from None
    finally:
        f.last[host] = time.time()
    if r.status_code in REFUSAL_CODES:
        f.refused[host] = f"HTTP {r.status_code}"
        raise Refused(f"{host}: HTTP {r.status_code}")
    return r.text


def text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


# ---- Idox Public Access ----------------------------------------------------------

def idox_details(f, summary_url):
    """Return {field: value} from the Further Information tab."""
    url = re.sub(r"activeTab=\w+", "activeTab=details", summary_url)
    page = f.get(url)
    return {text(k): text(v) for k, v in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", page, re.S)}, url


def idox_documents(f, summary_url):
    """Return [{date, type, description, url}] from the Documents tab."""
    url = re.sub(r"activeTab=\w+", "activeTab=documents", summary_url)
    page = f.get(url, cache=False)  # live load: sets the session cookie documents need
    base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(url))
    docs = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        link = re.search(r'href="([^"]*/files/[^"]+)"', row)
        if not link:
            continue
        cells = [text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        cells = [c for c in cells if c and c != "Select this document" and c != "View"]
        docs.append({"date": cells[0] if cells else "", "type": cells[1] if len(cells) > 1 else "",
                     "description": " | ".join(cells[2:]), "url": urllib.parse.urljoin(base, html.unescape(link.group(1)))})
    return docs, url


# ---- Ocella (Hillingdon) -----------------------------------------------------------

def ocella_details(f, details_url):
    page = f.get(details_url)
    pairs = re.findall(r"<td[^>]*>\s*<strong>(.*?)</strong>\s*</td>\s*<td[^>]*>(.*?)</td>", page, re.S)
    return {text(k): text(v) for k, v in pairs}, details_url


def ocella_documents(f, details_url):
    ref = urllib.parse.parse_qs(urllib.parse.urlparse(details_url).query)["reference"][0]
    url = "https://planning.hillingdon.gov.uk/OcellaWeb/showDocuments?module=pl&reference=" + urllib.parse.quote(ref, safe="")
    page = f.get(url, referer=details_url)
    docs = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        link = re.search(r'href\s*=\s*"(viewDocument[^"]+)"', row)
        if not link:
            continue
        cells = [text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        cells = [c for c in cells if c]
        docs.append({"date": cells[1] if len(cells) > 1 else "", "type": cells[0] if cells else "",
                     "description": cells[-1] if len(cells) > 2 else "",
                     "url": "https://planning.hillingdon.gov.uk/OcellaWeb/" + html.unescape(link.group(1))})
    return docs, url


HANDLERS = {
    "idox": (lambda u: "/online-applications/applicationDetails.do" in u, idox_details, idox_documents),
    "ocella": (lambda u: "/OcellaWeb/planningDetails" in u, ocella_details, ocella_documents),
}


def handler_for(url):
    for name, (match, det, docs) in HANDLERS.items():
        if url and match(url):
            return name, det, docs
    return None


# ---- Arcus "planning-register.co.uk" (Vale of White Horse, South Oxfordshire, Vale of Glamorgan) -----

def _arcus_page(f, url):
    page = f.get(url, cache=False)
    if "/Disclaimer/AcceptDisclaimer" in page and "__RequestVerificationToken" in page:
        base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(url))
        token = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', page).group(1)
        ret = re.search(r'name="returnURL" value="([^"]+)"', page).group(1)
        page = post(f, base + "/Disclaimer/AcceptDisclaimer",
                    {"returnURL": html.unescape(ret), "__RequestVerificationToken": token}, referer=url)
    tok = re.findall(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', page)
    if tok:
        f.tokens = getattr(f, "tokens", {})
        f.tokens[urllib.parse.urlparse(url).netloc] = tok[0]
    return page


def arcus_details(f, url):
    page = _arcus_page(f, url)
    pairs = re.findall(r"<(?:dt|th|label)[^>]*>(.*?)</(?:dt|th|label)>\s*<(?:dd|td|div|span)[^>]*>(.*?)</(?:dd|td|div|span)>",
                       page, re.S)
    return {text(k): text(v) for k, v in pairs if text(k)}, url


def arcus_documents(f, url):
    page = _arcus_page(f, url)
    base = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(url))
    docs = []
    for attrs, body in re.findall(r"<tr class='grid-dataRow[^']*'([^>]*)>(.*?)</tr>", page, re.S):
        a = dict(re.findall(r'data-(\w+)="([^"]*)"', attrs))
        cells = [text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", body, re.S)]
        cells = [c for c in cells if c and c not in ("-", "View")]
        docs.append({"date": cells[-1] if cells else "", "type": cells[-2] if len(cells) > 1 else "",
                     "description": cells[0] if cells else "",
                     "url": f"{base}/Document/GetFileBinary#{a.get('recordNumber')}/{a.get('planID')}/{a.get('imageID')}",
                     "post": {"module": a.get("module"), "recordNumber": a.get("recordNumber"),
                              "planID": int(float(a.get("planID", 0))), "imageID": int(float(a.get("imageID", 0))),
                              "isPlan": a.get("storedInDatabase", "False").lower() == "true"}})
    return docs, url


def download(f, doc, referer):
    """Fetch a document's bytes, using the portal's own public viewer mechanism."""
    if doc.get("post"):
        import base64
        host = urllib.parse.urlparse(doc["url"]).netloc
        raw = post(f, doc["url"].split("#")[0], doc["post"], referer=referer,
                   headers={"X-Requested-With": "XMLHttpRequest",
                            "RequestVerificationToken": getattr(f, "tokens", {}).get(host, "")})
        try:
            return base64.b64decode(raw.strip().strip('"'))
        except Exception:
            return raw.encode()
    return f.get(doc["url"], referer=referer, binary=True, cache=False)


HANDLERS["arcus"] = (lambda u: "planning-register.co.uk/Planning/Display" in u, arcus_details, arcus_documents)
