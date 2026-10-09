"""Index HM Land Registry CCOD/OCOD CSVs into SQLite (kept outside the repo: licensed data).

    python tools/lr_index.py OUT.sqlite CCOD.csv OCOD.csv
"""
import csv
import sqlite3
import sys

db = sqlite3.connect(sys.argv[1])
db.execute("""CREATE TABLE IF NOT EXISTS titles (title TEXT, tenure TEXT, address TEXT, district TEXT,
              postcode TEXT, source TEXT, date_added TEXT)""")
db.execute("""CREATE TABLE IF NOT EXISTS proprietors (title TEXT, n INT, name TEXT, company_number TEXT,
              category TEXT, country TEXT, address TEXT)""")
for path in sys.argv[2:]:
    src = "OCOD" if "OCOD" in path else "CCOD"
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        batch_t, batch_p = [], []
        for r in csv.DictReader(fh):
            r = {k: (v or "") for k, v in r.items() if k}
            t = r.get("Title Number", "")
            if not t:
                continue
            batch_t.append((t, r.get("Tenure", ""), r.get("Property Address", ""), r.get("District", ""),
                            r.get("Postcode", "").replace(" ", "").upper(), src, r.get("Date Proprietor Added", "")))
            for n in range(1, 5):
                name = r.get(f"Proprietor Name ({n})")
                if name:
                    addr = ", ".join(x for x in (r.get(f"Proprietor ({n}) Address ({k})") for k in (1, 2, 3)) if x)
                    num = (r.get(f"Company Registration No. ({n})") or "").strip().upper()
                    num = num.zfill(8) if num.isdigit() else num
                    batch_p.append((t, n, name, num, r.get(f"Proprietorship Category ({n})"),
                                    r.get(f"Country Incorporated ({n})", "United Kingdom"), addr))
            if len(batch_t) >= 50000:
                db.executemany("INSERT INTO titles VALUES (?,?,?,?,?,?,?)", batch_t)
                db.executemany("INSERT INTO proprietors VALUES (?,?,?,?,?,?,?)", batch_p)
                batch_t, batch_p = [], []
        db.executemany("INSERT INTO titles VALUES (?,?,?,?,?,?,?)", batch_t)
        db.executemany("INSERT INTO proprietors VALUES (?,?,?,?,?,?,?)", batch_p)
    db.commit()
    print(src, "loaded", flush=True)
for sql in ["CREATE INDEX IF NOT EXISTS t_title ON titles(title)", "CREATE INDEX IF NOT EXISTS t_pc ON titles(postcode)",
            "CREATE INDEX IF NOT EXISTS p_title ON proprietors(title)",
            "CREATE INDEX IF NOT EXISTS p_num ON proprietors(company_number)",
            "CREATE INDEX IF NOT EXISTS p_name ON proprietors(name)"]:
    db.execute(sql)
db.commit()
print(db.execute("select count(*) from titles").fetchone(), db.execute("select count(*) from proprietors").fetchone())
