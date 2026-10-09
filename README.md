# Data centre planning tracker: UK and Ireland

Every night this finds new and updated planning applications for data centres in the UK and Ireland, reads each one with Claude to pull out MW, size and stage, works out who ultimately owns the applicant, and records what changed.

## What you get

| File | What it is |
| --- | --- |
| `data/applications.csv` | One row per application: council, dates, status, stage, MW, floor area, owner, ownership chain, link |
| `data/changes/YYYY-MM-DD.md` | What's new or moved since the last run; the thing to read each morning |
| `data/known_owners.csv` | Name patterns mapped to parent companies. **Add special-purpose company names here as you find them**; this is the manual, proprietary layer |

## Sources

- **UK: PlanIt** (planit.org.uk) aggregates ~400 council planning portals. Free, rate-limited. **Email the operator to confirm commercial use at NSR is fine before relying on it.**
- **Ireland: National Planning Applications** (data.gov.ie), weekly, CC BY 4.0.
- **Owners, UK: Companies House API** (free key). Walks the "persons with significant control" chain upwards from the applicant company.
- Ireland has no free equivalent of that register, so Irish owners come from the name patterns only.

## Set-up (about 20 minutes)

1. Create a **private** GitHub repository and upload this folder.
2. Get two keys:
   - Anthropic API key: console.anthropic.com
   - Companies House API key: developer.company-information.service.gov.uk (create an application, choose "REST", copy the key)
3. In the repository: Settings → Secrets and variables → Actions:
   - Secrets: `ANTHROPIC_API_KEY`, `COMPANIES_HOUSE_API_KEY`
   - Variables: `TRACKER_USER_AGENT`, e.g. `dc-planning-tracker (your.email@domain)`
4. Actions tab → "Nightly planning tracker" → Run workflow → tick **backfill** for the first run (two years of history).
5. After that it runs at 03:17 UTC daily and commits results to `data/`.

Run locally instead: `pip install -r requirements.txt`, set the same environment variables, then `python -m tracker.main --backfill`.

## How it works

1. `tracker/sources/planit.py`, `ireland.py`: keyword search (phrases in `tracker/config.py`), normalised to one schema.
2. `tracker/extract.py`: Claude classifies each application (real data centre vs a server room), and extracts stage, MW, floor area and halls **only where the text states them**. Cached, so each application is only read once unless its text changes.
3. `tracker/owners.py`: known-name match first, then the Companies House chain for UK applicants.
4. `tracker/store.py`: merges into the master CSV, flags new rows and changes to status, decision, stage or decision date.

## Known limits

- PlanIt coverage depends on each council's portal; some councils are missing or lag.
- Descriptions rarely state MW. Floor area and hall count are more common; MW stays blank rather than guessed.
- Owner confidence is "high" only when the Companies House name match is exact and the chain hits a known parent. Check "low" rows by hand.
- Large UK schemes that opt into the national infrastructure route (possible since Jan 2026) go to the Planning Inspectorate, not councils. They are not covered yet.
- Field names on the Irish service are discovered at run time; if they change, the run fails loudly with the available field list.

## Tests

`python -m pytest -q` runs offline with fixtures (no keys or network needed).
