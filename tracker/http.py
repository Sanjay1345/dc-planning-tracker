"""Polite HTTP helper: one session, a user agent, pauses and retries on 429/5xx."""

import time

import requests

from . import config

_session = requests.Session()
_session.headers["User-Agent"] = config.USER_AGENT


def get_json(url, params=None, auth=None, retries=4):
    for attempt in range(retries):
        resp = _session.get(url, params=params, auth=auth, timeout=60)
        if resp.status_code in (429, 500, 502, 503, 504):
            wait = int(resp.headers.get("Retry-After", 0)) or 10 * (attempt + 1)
            time.sleep(wait)
            continue
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        time.sleep(config.REQUEST_PAUSE_SECS)
        return resp.json()
    raise RuntimeError(f"Gave up on {url} after {retries} attempts")
