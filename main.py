"""Run the tracker: fetch -> extract -> resolve owners -> merge -> change log.

    python -m tracker.main              # nightly run (last 14 days)
    python -m tracker.main --backfill   # first run (last 2 years)
    python -m tracker.main --sources uk # one source only
"""

import argparse
import sys
import traceback

from . import config, extract, owners, store
from .sources import ireland, planit

SOURCES = {"uk": planit, "ie": ireland}


def run(source_keys, days, do_extract=True):
    fresh, failures = [], []
    for key in source_keys:
        try:
            rows = SOURCES[key].fetch(days)
            print(f"{key}: {len(rows)} matching applications")
            fresh += rows
        except Exception:  # one broken source must not stop the others
            failures.append(key)
            traceback.print_exc()

    if do_extract:
        cache = store.load_cache("extract")
        extract.extract(fresh, cache)
        store.save_cache("extract", cache)

    ch_cache = store.load_cache("companies")
    owners.resolve(fresh, owners.load_known(), ch_cache)
    store.save_cache("companies", ch_cache)

    master, new_ids, changes = store.merge(store.load_master(), fresh)
    store.save_master(master)
    path, n_new, n_moved = store.write_changes(master, new_ids, changes)
    print(f"{n_new} new data centre applications, {n_moved} status changes -> {path}")
    return failures


def cli(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--backfill", action="store_true")
    p.add_argument("--days", type=int)
    p.add_argument("--sources", default="uk,ie")
    p.add_argument("--no-extract", action="store_true")
    a = p.parse_args(argv)
    days = a.days or (config.BACKFILL_DAYS if a.backfill else config.NIGHTLY_LOOKBACK_DAYS)
    failures = run([s.strip() for s in a.sources.split(",")], days, not a.no_extract)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    cli()
