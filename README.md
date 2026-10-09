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
- PlanIt withholds applicant names (it returns "See source"), and the Irish service leaves them blank. The Companies House step therefore rarely has a name to look up; owners mostly come from names in the description and from `data/known_owners.csv`.
- PlanIt has no coordinates for most UK rows, and its status can lag the council (e.g. Cambois 24/04112/OUTES still shows "Undecided" although later records cite the approved outline).
- Rows with `extract_hash` starting `manual` were classified by hand; nightly runs keep their stage, size, project and owner fields. `is_data_centre = possible` marks flexible-use schemes (e.g. "B2/B8 or data centre") and is left out of the change log.
- `project` groups the many records (conditions, consultations from neighbouring councils, amendments) that belong to one scheme.

## Tests

`python -m pytest -q` runs offline with fixtures (no keys or network needed).

## Ownership evidence (top projects)

`tools/` builds an evidence chain for ownership from exact identifiers rather than name matching. It is run by hand, not nightly:

1. `collect_evidence.py`: reads public council portal pages (Idox, Hillingdon Ocella, planning-register.co.uk) for applicant and agent, then downloads S106 agreements, deeds, cover letters, application forms and ownership certificates. `evidence.py` pulls company numbers, overseas-entity IDs, title numbers and agreement parties, each with a quote; it uses OCR only when a PDF has no text layer. The fetcher sends one request per host every 4 s and never retries after a refusal.
2. `lr_index.py`: indexes HM Land Registry CCOD/OCOD (licensed; kept outside the repo). Title numbers quoted in documents are looked up exactly; site-address matches are recorded only when corroborated.
3. `ch_chain.py`: Companies House PSC chain to the top entity, plus charges (lenders, title numbers in charge descriptions). Every step records why the chain stops.
4. `assemble_ownership.py`: writes the columns below to `data/applications.csv`.

| Column | Meaning |
| --- | --- |
| `company_number` | Main entity: a developer/interested party or owner named in a legal agreement, else the registered proprietor |
| `ownership_chain` / `owner_parent` | Companies House PSC chain and its top entity |
| `title_numbers` | Title numbers quoted in documents, or held by a corroborated proprietor; address-only ones marked "(low)" |
| `landowner` | Registered proprietors (name, company number, confidence) |
| `lender` | Mortgagees named in agreements, plus outstanding chargees from Companies House |
| `evidence` | URL plus quoted text for each link |
| `confidence` | `high`: an exact number or title quoted in the site's documents. `medium`: address-matched title whose proprietor's number also appears in the documents. `low`: name only, or address match with a data-centre proprietor name. Press reports and memory are never used. |

Nightly runs never overwrite rows that carry `evidence`.

Contains HM Land Registry data © Crown copyright and database right 2026. Contains Companies House data licensed under the Open Government Licence v3.0.
