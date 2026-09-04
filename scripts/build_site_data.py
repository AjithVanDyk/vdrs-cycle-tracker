r"""Generate the VDRS Cycle Classification Tracker HTML dashboard.

Desktop-focused executive dashboard with full filtering options (All, Completed, Ready to Push, Needs Review, P1, P2, P3, HOLD)
and interactive table column sorting (Part #, Description, Item Class, Code Shift, Status, Stock Value).
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
try:
    import db
    HAS_DB = True
except Exception:
    HAS_DB = False

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = REPO_ROOT.parent

DATA_DIR = REPO_ROOT / "data"
CHANGES_PATHS = [
    DATA_DIR / "changes.csv",
    WORKSPACE_ROOT / "24_Project_Cycle_Reclass_Chris" / "04_Change_Tracker" / "changes.csv"
]
CSV_PATH = next((p for p in CHANGES_PATHS if p.exists()), DATA_DIR / "changes.csv")

LIVE_PATHS = [
    DATA_DIR / "live_cycle.csv",
    WORKSPACE_ROOT / "24_Project_Cycle_Reclass_Chris" / "04_Change_Tracker" / "live_cycle.csv"
]
LIVE_CYCLE_PATH = next((p for p in LIVE_PATHS if p.exists()), DATA_DIR / "live_cycle.csv")

CATALOG_BASELINE = DATA_DIR / "catalog_baseline.csv"

# NetStock: glob newest Stock holding.xlsx across all refresh folders
def _find_netstock():
    export_dir = WORKSPACE_ROOT / "11_Data_NetStock_Exports"
    if not export_dir.exists():
        return None
    try:
        candidates = list(export_dir.rglob("Stock holding.xlsx"))
        candidates = [p for p in candidates if not p.name.startswith("~$")]
        return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None
    except Exception:
        return None

EMAIL_SUMMARY = WORKSPACE_ROOT / "35_Tool_Email_Knowledge_Base" / "extracted" / "parts_summary.csv"

OUT_PATHS = [
    REPO_ROOT / "docs" / "index.html",
]


def fetch_data():
    df = None
    if HAS_DB:
        try:
            cn = db.connect()
            if cn:
                sql = "SELECT Part, Description, ItemClass, Supplier, CycleFrom, CycleTo, ReasonWord, ReasonDetail, ApprovedBy, DecidedOn, SourceFile, SourceSheet, OnHand, StockValue, ReorderPoint, SetReorderTo, LiveCycle, AppliedInAcumatica, AppliedAt, LastCheckedAt, UpdatedAt FROM dbo.CycleChangeLog"
                df = pd.read_sql(sql, cn)
                cn.close()
                print(f"Loaded {len(df)} rows from SQL Server dbo.CycleChangeLog")
        except Exception as e:
            print(f"SQL read warning ({e}), using changes.csv fallback")

    if df is None or df.empty:
        if CSV_PATH.exists():
            df = pd.read_csv(CSV_PATH, dtype=str)
            print(f"Loaded {len(df)} rows from {CSV_PATH.name}")
        else:
            raise RuntimeError(f"No changes data found (checked SQL Server and {CSV_PATH})")

    if LIVE_CYCLE_PATH.exists():
        live = pd.read_csv(LIVE_CYCLE_PATH, dtype=str, low_memory=False)
        live["part"] = live["part"].str.strip()
        m = live.set_index("part")["live_cycle"]
        df["LiveCycle"] = df["Part"].str.strip().map(m)

        def calc_status(r):
            if getattr(r, "ApprovedBy", None) and "Pending Review" in str(r.ApprovedBy):
                return "PENDING_REVIEW"
            if pd.isna(r.LiveCycle):
                return "UNKNOWN"
            return "YES" if str(r.LiveCycle).strip() == str(r.CycleTo).strip() else "NO"

        df["AppliedInAcumatica"] = df.apply(calc_status, axis=1)
        print("Applied live cycle status check: " + str(dict(df["AppliedInAcumatica"].value_counts())))

    return df


def fetch_full_catalog(tracker_df):
    """Join live_cycle + NetStock Stock holding + changes + email KB into 25,914-row catalog."""
    # If in GitHub Actions / standalone environment where baseline is available and NetStock is not
    ns_path = _find_netstock()
    if not ns_path and CATALOG_BASELINE.exists():
        print(f"Loading catalog base from {CATALOG_BASELINE.name}")
        cat = pd.read_csv(CATALOG_BASELINE, dtype=str, low_memory=False)
        cat["part"] = cat["part"].str.strip()
        
        # Update live cycle & reclass status on top of baseline
        if LIVE_CYCLE_PATH.exists():
            live = pd.read_csv(LIVE_CYCLE_PATH, dtype=str, low_memory=False)
            live["part"] = live["part"].str.strip()
            live_map = live.set_index("part")["live_cycle"]
            cat["live_cycle"] = cat["part"].map(live_map).fillna(cat["live_cycle"]).fillna("")

        reclass = tracker_df[["Part", "CycleTo", "AppliedInAcumatica"]].copy()
        reclass["Part"] = reclass["Part"].astype(str).str.strip()
        reclass = reclass.drop_duplicates("Part", keep="first")
        reclass.columns = ["part", "rtarget", "rstatus"]
        
        cat = cat.drop(columns=["rtarget", "rstatus"], errors="ignore")
        cat = cat.merge(reclass, on="part", how="left")
        cat["rtarget"] = cat["rtarget"].fillna("")
        cat["rstatus"] = cat["rstatus"].fillna("")

        for col in ["onhand", "val", "lt", "fc", "hits", "emails", "crit", "md", "exp", "obs", "bo"]:
            cat[col] = pd.to_numeric(cat[col], errors="coerce").fillna(0.0)

        cat = cat.sort_values("part")
        print(f"Full catalog built from baseline: {len(cat)} rows")
        return cat

    # --- Base: every live part from Acumatica ---
    live = pd.read_csv(LIVE_CYCLE_PATH, dtype=str, low_memory=False)
    live["part"] = live["part"].str.strip()

    # --- NetStock Stock holding ---
    if ns_path:
        ns = pd.read_excel(ns_path)
        ns["part"] = ns["Product code"].astype(str).str.strip()
        ns_clean = pd.DataFrame({
            "part": ns["part"],
            "desc": ns["Description"].fillna("").astype(str).str.strip(),
            "supp": ns["Supplier description"].fillna("").astype(str).str.strip(),
            "iclass": ns["Item Class"].fillna("").astype(str).str.strip(),
            "onhand": pd.to_numeric(ns["On hand"], errors="coerce").fillna(0.0),
            "val": pd.to_numeric(ns["On hand value"], errors="coerce").fillna(0.0),
            "nsclass": ns["Classification"].fillna("").astype(str).str.strip(),
            "status": ns["Status"].fillna("").astype(str).str.strip(),
            "lt": pd.to_numeric(ns["LT days"], errors="coerce").fillna(0.0),
            "fc": pd.to_numeric(ns["Average forecasted sales"], errors="coerce").fillna(0.0),
            "hits": pd.to_numeric(ns["Hits"], errors="coerce").fillna(0.0),
        })
        print(f"Loaded {len(ns)} rows from NetStock: {ns_path.name}")
    else:
        ns_clean = pd.DataFrame(columns=["part", "desc", "supp", "iclass", "onhand", "val", "nsclass", "status", "lt", "fc", "hits"])
        print("WARNING: no Stock holding.xlsx found")

    # --- Email Knowledge Base ---
    if EMAIL_SUMMARY.exists():
        em = pd.read_csv(EMAIL_SUMMARY, dtype=str, low_memory=False)
        em["part"] = em["part"].str.strip()
        em_clean = pd.DataFrame({
            "part": em["part"],
            "emails": pd.to_numeric(em["emails"], errors="coerce").fillna(0.0),
            "crit": pd.to_numeric(em["critical"], errors="coerce").fillna(0.0),
            "md": pd.to_numeric(em["machine_down"], errors="coerce").fillna(0.0),
            "exp": pd.to_numeric(em["expedite"], errors="coerce").fillna(0.0),
            "obs": pd.to_numeric(em["obsolete"], errors="coerce").fillna(0.0),
            "bo": pd.to_numeric(em["backorder"], errors="coerce").fillna(0.0),
        })
        print(f"Loaded {len(em)} parts from email KB")
    else:
        em_clean = pd.DataFrame(columns=["part", "emails", "crit", "md", "exp", "obs", "bo"])
        print("WARNING: parts_summary.csv not found")

    # --- Reclass status from tracker_df ---
    reclass = tracker_df[["Part", "CycleTo", "AppliedInAcumatica", "ItemClass"]].copy()
    reclass["Part"] = reclass["Part"].astype(str).str.strip()
    reclass = reclass.drop_duplicates("Part", keep="first")
    reclass.columns = ["part", "rtarget", "rstatus", "ch_iclass"]

    # --- Join everything onto the live_cycle base ---
    cat = live.merge(ns_clean, on="part", how="left")
    cat = cat.merge(em_clean, on="part", how="left")
    cat = cat.merge(reclass, on="part", how="left")

    # Fill defaults and fallbacks
    cat["desc"] = cat["desc"].fillna("")
    cat["supp"] = cat["supp"].fillna("")
    cat["iclass"] = cat["iclass"].replace(["nan", "None", "", "NaN"], pd.NA).fillna(cat["ch_iclass"]).fillna("")
    cat["nsclass"] = cat["nsclass"].fillna("")
    cat["status"] = cat["status"].fillna("")
    cat["rstatus"] = cat["rstatus"].fillna("")
    cat["rtarget"] = cat["rtarget"].fillna("")

    for col in ["onhand", "val", "lt", "fc", "hits", "emails", "crit", "md", "exp", "obs", "bo"]:
        cat[col] = pd.to_numeric(cat[col], errors="coerce").fillna(0.0)

    cat = cat.sort_values("part")
    print(f"Full catalog built: {len(cat)} rows")
    return cat


def clean_str(val):
    if val is None or pd.isna(val):
        return ""
    s = str(val).strip()
    s = s.replace("—", " - ").replace("–", " - ").replace("--", ": ")
    return s


def generate_html(df, catalog_df):
    df["Part"] = df["Part"].apply(clean_str)
    df["Description"] = df["Description"].apply(clean_str)
    df["ItemClass"] = df["ItemClass"].apply(clean_str)
    df["Supplier"] = df["Supplier"].apply(clean_str)
    df["CycleFrom"] = df["CycleFrom"].apply(clean_str)
    df["CycleTo"] = df["CycleTo"].apply(clean_str)
    df["ReasonWord"] = df["ReasonWord"].apply(clean_str)
    df["ReasonDetail"] = df["ReasonDetail"].apply(clean_str)
    df["ApprovedBy"] = df["ApprovedBy"].apply(clean_str)
    df["DecidedOn"] = df["DecidedOn"].apply(clean_str)
    df["SourceSheet"] = df["SourceSheet"].apply(clean_str)
    df["AppliedInAcumatica"] = df["AppliedInAcumatica"].apply(clean_str)
    
    df["OnHand"] = pd.to_numeric(df["OnHand"], errors="coerce").fillna(0.0)
    df["StockValue"] = pd.to_numeric(df["StockValue"], errors="coerce").fillna(0.0)

    # Sort chronologically by DecidedOn descending, then Part
    df = df.sort_values(by=["DecidedOn", "Part"], ascending=[False, True])

    # Counts & Metrics
    total_count = len(df)
    completed_df = df[df["AppliedInAcumatica"] == "YES"]
    pending_push_df = df[df["AppliedInAcumatica"] == "NO"]
    pending_review_df = df[df["AppliedInAcumatica"] == "PENDING_REVIEW"]

    completed_count = len(completed_df)
    pending_push_count = len(pending_push_df)
    pending_review_count = len(pending_review_df)
    total_val = df["StockValue"].sum()

    ajith_classified_count = df["ItemClass"].str.endswith("*").sum()

    # Category breakdowns
    code_counts = df["CycleTo"].value_counts().to_dict()
    val_by_tier = df.groupby("CycleTo")["StockValue"].sum().to_dict()

    # Build JSON array for frontend
    df["ReorderPoint"] = pd.to_numeric(df["ReorderPoint"], errors="coerce").fillna(0.0)
    df["SetReorderTo"] = pd.to_numeric(df["SetReorderTo"], errors="coerce")

    records = []
    for r in df.itertuples(index=False):
        records.append([
            r.Part,           # 0
            r.Description,    # 1
            r.ItemClass,      # 2
            r.Supplier,       # 3
            r.CycleFrom,      # 4
            r.CycleTo,        # 5
            r.AppliedInAcumatica,  # 6
            r.ReasonWord,     # 7
            r.ReasonDetail,   # 8
            r.ApprovedBy,     # 9
            r.DecidedOn,      # 10
            r.SourceSheet,    # 11
            round(r.OnHand, 2),          # 12
            round(r.StockValue, 2),      # 13
            round(r.ReorderPoint, 2),    # 14
            None if pd.isna(r.SetReorderTo) else round(r.SetReorderTo, 2)  # 15
        ])

    data_json = json.dumps(records)

    # --- Full catalog JSON (25k rows) ---
    cat_records = []
    for d in catalog_df.to_dict("records"):
        cat_records.append([
            str(d["part"]).strip(),                       # 0  part
            str(d["desc"]).strip(),                       # 1  description
            str(d["supp"]).strip(),                       # 2  supplier
            str(d["iclass"]).strip(),                     # 3  item class
            str(d["live_cycle"]).strip(),                 # 4  live cycle
            str(d["nsclass"]).strip(),                    # 5  ns classification
            str(d["status"]).strip(),                     # 6  ns status
            round(float(d["onhand"]), 0),                 # 7  on hand
            round(float(d["val"]), 2),                    # 8  on hand value
            round(float(d["fc"]), 2),                     # 9  avg forecast/mo
            int(d["hits"]),                               # 10 hits
            int(d["lt"]),                                 # 11 lt days
            int(d["emails"]),                             # 12 total emails
            int(d["crit"]),                               # 13 critical
            int(d["md"]),                                 # 14 machine down
            int(d["exp"]),                                # 15 expedite
            int(d["obs"]),                                # 16 obsolete
            int(d["bo"]),                                 # 17 backorder
            str(d["rstatus"]).strip(),                    # 18 reclass status
            str(d["rtarget"]).strip(),                    # 19 reclass target
        ])

    catalog_json = json.dumps(cat_records)

    # --- Catalog KPI counts ---
    c_total = len(cat_records)
    c_email = sum(1 for r in cat_records if r[12] > 0)
    c_stockedout = sum(1 for r in cat_records if r[6] == "Stocked out")
    c_excess = sum(1 for r in cat_records if r[6] == "Excess stock")
    c_nodecision = sum(1 for r in cat_records if not r[18])

    # cycle breakdown for catalog filter chip counts
    from collections import Counter
    cycle_counts = Counter(r[4] for r in cat_records)
    has_email_count = c_email

    as_of = datetime.now().strftime("%B %d, %Y")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VDRS Parts Cycle Classification Tracker</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📊</text></svg>">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {{
      --bg: #0b0f19;
      --card-bg: #131b2e;
      --card-border: #23314d;
      --text-main: #ffffff;
      --text-sub: #94a3b8;
      
      --green: #10b981;
      --amber: #f59e0b;
      --purple: #a855f7;
      --red: #ef4444;
      --blue: #38bdf8;
      --gray: #64748b;
    }}

    * {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      background: var(--bg);
      background-image: radial-gradient(ellipse at top, #1c2744 0%, #0b0f19 70%);
      color: var(--text-main);
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      padding: 24px 32px;
      min-width: 100%;
    }}

    .wrap {{
      max-width: 1600px;
      width: 100%;
      margin: 0 auto;
    }}

    /* Top Header */
    header.header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      margin-bottom: 24px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--card-border);
    }}

    .brand-tag {{
      background: #38bdf8;
      color: #000;
      font-weight: 800;
      font-size: 0.8rem;
      padding: 3px 8px;
      border-radius: 4px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}

    h1 {{
      font-size: 1.85rem;
      font-weight: 800;
      letter-spacing: -0.02em;
      margin-top: 4px;
    }}

    p.subhead {{
      font-size: 0.95rem;
      color: var(--text-sub);
      margin-top: 4px;
    }}

    .as-of-tag {{
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--card-border);
      padding: 8px 16px;
      border-radius: 20px;
      font-size: 0.82rem;
      color: var(--text-sub);
    }}

    .footnote-banner {{
      display: flex;
      align-items: center;
      gap: 12px;
      background: rgba(56, 189, 248, 0.08);
      border: 1px solid rgba(56, 189, 248, 0.25);
      padding: 12px 18px;
      border-radius: 12px;
      margin-bottom: 24px;
      color: #cbd5e1;
      font-size: 0.88rem;
    }}

    /* 3 Big Metric Cards */
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 28px;
    }}

    .summary-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 22px 24px;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
      position: relative;
      overflow: hidden;
    }}

    .summary-card:hover {{
      transform: translateY(-2px);
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
    }}

    .summary-card.done {{ border-left: 5px solid var(--green); }}
    .summary-card.push {{ border-left: 5px solid var(--amber); }}
    .summary-card.review {{ border-left: 5px solid var(--purple); }}
    .summary-card.value {{ border-left: 5px solid var(--blue); cursor: default; }}

    .card-label {{
      font-size: 0.82rem;
      font-weight: 700;
      color: var(--text-sub);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 8px;
    }}

    .card-number {{
      font-size: 2.3rem;
      font-weight: 800;
      line-height: 1;
      margin-bottom: 8px;
      font-variant-numeric: tabular-nums;
    }}

    .card-desc {{
      font-size: 0.85rem;
      color: var(--text-sub);
    }}

    .card-dl {{
      margin-top: 12px;
      width: 100%;
      justify-content: center;
      font-size: 0.8rem;
      padding: 7px 12px;
    }}

    /* Charts Row */
    .graphs-grid {{
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      gap: 20px;
      margin-bottom: 28px;
    }}

    .graph-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 24px;
      display: flex;
      flex-direction: column;
    }}

    .graph-title {{
      font-size: 1.05rem;
      font-weight: 700;
      margin-bottom: 4px;
    }}

    .graph-sub {{
      font-size: 0.82rem;
      color: var(--text-sub);
      margin-bottom: 16px;
    }}

    .chart-box {{
      position: relative;
      flex: 1;
      min-height: 220px;
      width: 100%;
    }}

    /* 4 Tier Cards */
    .tiers-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 28px;
    }}

    .tier-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 18px 20px;
      cursor: pointer;
      transition: all 0.2s;
    }}

    .tier-card:hover {{
      border-color: #38bdf8;
      background: #17223b;
    }}

    .t-header {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
    }}

    .dot {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
    }}

    .t-name {{
      font-weight: 700;
      font-size: 0.92rem;
    }}

    .t-count {{
      font-size: 1.35rem;
      font-weight: 800;
      margin-bottom: 4px;
    }}

    .t-val {{
      font-size: 0.82rem;
      color: var(--text-sub);
    }}

    .dl-btn {{
      background: #1e3a5f;
      border: 1px solid #38bdf8;
      color: #38bdf8;
      padding: 8px 16px;
      border-radius: 8px;
      font-size: 0.84rem;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.15s;
      white-space: nowrap;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}

    .dl-btn:hover {{
      background: #38bdf8;
      color: #0b0f19;
      box-shadow: 0 0 12px rgba(56, 189, 248, 0.4);
    }}

    /* Controls & Search */
    .controls-row {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 18px;
      gap: 16px;
      flex-wrap: wrap;
    }}

    .filter-buttons {{
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }}

    .f-btn {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text-sub);
      padding: 8px 14px;
      border-radius: 8px;
      font-size: 0.84rem;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.15s;
    }}

    .f-btn:hover {{
      color: #fff;
      border-color: #38bdf8;
    }}

    .f-btn.active {{
      background: #38bdf8;
      color: #000;
      border-color: #38bdf8;
    }}

    .search-box {{
      position: relative;
      width: 380px;
    }}

    .search-box input {{
      width: 100%;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      padding: 11px 16px 11px 40px;
      color: #fff;
      font-size: 0.9rem;
      outline: none;
    }}

    .search-box input:focus {{
      border-color: #38bdf8;
    }}

    .search-icon-svg {{
      position: absolute;
      left: 14px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-sub);
    }}

    /* Large Data Table */
    .table-card {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      overflow: auto;
      max-height: 72vh;
      min-height: 480px;
      -webkit-overflow-scrolling: touch;
      box-shadow: 0 8px 32px rgba(0,0,0,0.35);
    }}

    table {{
      width: 100%;
      min-width: 1480px;
      border-collapse: separate;
      border-spacing: 0;
      font-size: 0.88rem;
      text-align: left;
    }}

    th {{
      background: #0f1626;
      padding: 14px 16px;
      color: var(--text-sub);
      font-weight: 700;
      font-size: 0.76rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      border-bottom: 2px solid var(--card-border);
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
      position: sticky;
      top: 0;
      z-index: 10;
      transition: color 0.15s, background-color 0.15s;
    }}

    th:hover {{
      color: #38bdf8;
      background: #151f36;
    }}

    th.sort-active {{
      color: #38bdf8;
    }}

    td {{
      padding: 13px 16px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
      vertical-align: middle;
    }}

    tr:hover td {{
      background: rgba(56, 189, 248, 0.05);
    }}

    .p-code {{
      font-weight: 800;
      color: #38bdf8;
      font-family: monospace;
      font-size: 0.95rem;
    }}

    .p-desc {{
      font-weight: 500;
      color: #f1f5f9;
      max-width: 320px;
    }}

    .p-class-starred {{
      color: #f59e0b;
      font-weight: 700;
    }}

    /* Big Status Pills */
    .pill {{
      display: inline-block;
      padding: 6px 14px;
      border-radius: 20px;
      font-size: 0.8rem;
      font-weight: 800;
      letter-spacing: 0.03em;
    }}

    .pill-done {{ background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid #10b981; }}
    .pill-push {{ background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid #f59e0b; }}
    .pill-review {{ background: rgba(168, 85, 247, 0.2); color: #c084fc; border: 1px solid #a855f7; }}

    .pill-code {{
      padding: 4px 10px;
      border-radius: 6px;
      font-weight: 800;
      font-size: 0.82rem;
    }}

    .code-p1 {{ background: rgba(239, 68, 68, 0.2); color: #fca5a5; }}
    .code-p2 {{ background: rgba(56, 189, 248, 0.2); color: #7dd3fc; }}
    .code-p3 {{ background: rgba(148, 163, 184, 0.2); color: #cbd5e1; }}
    .code-hold {{ background: rgba(168, 85, 247, 0.2); color: #e9d5ff; }}

    /* Pagination */
    .pagination-bar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 16px 24px;
      background: #0f1626;
      border-top: 1px solid var(--card-border);
      color: var(--text-sub);
      font-size: 0.88rem;
      font-weight: 600;
    }}

    .pg-btn {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: #fff;
      padding: 8px 18px;
      border-radius: 8px;
      font-weight: 700;
      font-size: 0.85rem;
      cursor: pointer;
    }}

    .pg-btn:disabled {{
      opacity: 0.3;
      cursor: not-allowed;
    }}

    /* Tab Navigation */
    .tab-bar {{
      display: flex;
      gap: 4px;
      margin-bottom: 28px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 5px;
    }}
    .tab-btn {{
      flex: 1;
      padding: 12px 20px;
      border: none;
      border-radius: 9px;
      font-size: 0.9rem;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.18s;
      background: transparent;
      color: var(--text-sub);
    }}
    .tab-btn.active {{
      background: #1e3a5f;
      color: #fff;
      box-shadow: 0 2px 8px rgba(0,0,0,0.4);
    }}
    .tab-btn:hover:not(.active) {{ color: #fff; }}

    /* Catalog-specific */
    .cat-kpi-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 24px;
    }}
    .cat-kpi {{
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 18px 20px;
    }}
    .cat-kpi .lbl {{ font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-sub); margin-bottom: 6px; }}
    .cat-kpi .num {{ font-size: 2rem; font-weight: 800; line-height: 1; }}

    .badge {{
      display: inline-block;
      padding: 2px 7px;
      border-radius: 5px;
      font-size: 0.75rem;
      font-weight: 700;
      margin: 1px;
    }}
    .badge-red   {{ background: rgba(239,68,68,0.18); color: #fca5a5; }}
    .badge-amber {{ background: rgba(245,158,11,0.18); color: #fde68a; }}
    .badge-blue  {{ background: rgba(56,189,248,0.18); color: #7dd3fc; }}
    .badge-gray  {{ background: rgba(100,116,139,0.18); color: #94a3b8; }}
    .badge-purple{{ background: rgba(168,85,247,0.18); color: #e9d5ff; }}

    .reclass-done   {{ font-size:0.78rem; font-weight:700; color:#34d399; }}
    .reclass-push   {{ font-size:0.78rem; font-weight:700; color:#fbbf24; }}
    .reclass-review {{ font-size:0.78rem; font-weight:700; color:#c084fc; }}
  </style>
</head>
<body>
  <div class="wrap">

    <!-- Tab Navigation -->
    <div class="tab-bar">
      <button class="tab-btn active" id="tab-tracker" onclick="switchTab('tracker')">📋 Parts Reclassification Tracker ({total_count:,} parts)</button>
      <button class="tab-btn" id="tab-catalog" onclick="switchTab('catalog')">📦 Full Master Catalog ({c_total:,} SKUs)</button>
    </div>

    <!-- ═══════════════ TRACKER VIEW ═══════════════ -->
    <div id="tracker-view">
    <!-- Top Header -->
    <header class="header">
      <div>
        <div style="display:flex; align-items:center; gap:10px; margin-bottom:4px;">
          <span class="brand-tag">VDRS</span>
          <span style="font-size:0.85rem; color:#10b981; font-weight:700;">● Live SQL Sync</span>
        </div>
        <h1>Parts Cycle Classification Tracker</h1>
        <p class="subhead">Live operational tracking of cycle code reclassifications across all {total_count:,} parts.</p>
      </div>

      <div class="as-of-tag">
        Updated: <strong>{as_of}</strong>
      </div>
    </header>

    <!-- Starred Classification Banner -->
    <div class="footnote-banner">
      <span>ℹ️</span>
      <span><strong>Note on Item Classes (*):</strong> Item classes marked with an asterisk (<strong>*</strong>) represent <strong>{ajith_classified_count:,} parts</strong> classified directly by <strong>Ajith Srikanth</strong> based on Acumatica item master data & product specifications.</span>
    </div>

    <!-- 3 Big Executive Metric Cards -->
    <div class="summary-grid">
      <div class="summary-card done" onclick="setFilterByCard('YES')">
        <div class="card-label">1. Live in Acumatica</div>
        <div class="card-number" style="color:#34d399;">{completed_count:,}</div>
        <div class="card-desc">Verified active in Acumatica ERP</div>
      </div>

      <div class="summary-card push" onclick="setFilterByCard('NO')">
        <div class="card-label">2. Ready to Push</div>
        <div class="card-number" style="color:#fbbf24;">{pending_push_count:,}</div>
        <div class="card-desc">Approved decisions ready for bulk import</div>
        <button class="dl-btn card-dl" onclick="event.stopPropagation(); downloadAcumatica('NO')"
          title="Download all Ready to Push rows as Acumatica bulk import CSV">
          ⬇ Export Acumatica Import ({pending_push_count:,} parts)
        </button>
      </div>

      <div class="summary-card review" onclick="setFilterByCard('PENDING_REVIEW')">
        <div class="card-label">3. Pending Review</div>
        <div class="card-number" style="color:#c084fc;">{pending_review_count:,}</div>
        <div class="card-desc">Open engineering & field clarification items</div>
      </div>

      <div class="summary-card value">
        <div class="card-label">Total Tracked Stock Value</div>
        <div class="card-number" style="color:#38bdf8;">${total_val:,.0f}</div>
        <div class="card-desc">On-hand inventory valuation at cost</div>
      </div>
    </div>

    <!-- 2 Clear Visual Charts -->
    <div class="graphs-grid">
      <div class="graph-card">
        <div class="graph-title">Review Progress Status</div>
        <div class="graph-sub">Where parts stand in the reclassification pipeline</div>
        <div class="chart-box">
          <canvas id="statusChart"></canvas>
        </div>
      </div>

      <div class="graph-card">
        <div class="graph-title">Target Classification Breakdown</div>
        <div class="graph-sub">Distribution of catalog parts across target cycle codes</div>
        <div class="chart-box">
          <canvas id="tierChart"></canvas>
        </div>
      </div>
    </div>

    <!-- 4 Clear Tier Cards -->
    <div class="tiers-grid">
      <div class="tier-card" onclick="setFilterByCard('P1')">
        <div class="t-header">
          <div class="dot" style="background:#ef4444;"></div>
          <div class="t-name">P1 Critical Spares</div>
        </div>
        <div class="t-count" style="color:#fca5a5;">{code_counts.get("P1", 0):,} parts</div>
        <div class="t-val">${val_by_tier.get("P1", 0.0):,.0f} stock value</div>
      </div>

      <div class="tier-card" onclick="setFilterByCard('P2')">
        <div class="t-header">
          <div class="dot" style="background:#38bdf8;"></div>
          <div class="t-name">P2 Managed Stock</div>
        </div>
        <div class="t-count" style="color:#7dd3fc;">{code_counts.get("P2", 0):,} parts</div>
        <div class="t-val">${val_by_tier.get("P2", 0.0):,.0f} stock value</div>
      </div>

      <div class="tier-card" onclick="setFilterByCard('P3')">
        <div class="t-header">
          <div class="dot" style="background:#94a3b8;"></div>
          <div class="t-name">P3 Buy on Demand</div>
        </div>
        <div class="t-count" style="color:#cbd5e1;">{code_counts.get("P3", 0):,} parts</div>
        <div class="t-val">${val_by_tier.get("P3", 0.0):,.0f} stock value</div>
      </div>

      <div class="tier-card" onclick="setFilterByCard('HOLD')">
        <div class="t-header">
          <div class="dot" style="background:#a855f7;"></div>
          <div class="t-name">HOLD / Field Review</div>
        </div>
        <div class="t-count" style="color:#e9d5ff;">{code_counts.get("HOLD", 0):,} parts</div>
        <div class="t-val">${val_by_tier.get("HOLD", 0.0):,.0f} stock value</div>
      </div>
    </div>

    <!-- Complete Filter Buttons & Search -->
    <div class="controls-row">
      <div class="filter-buttons">
        <button class="f-btn active" data-filter="ALL" onclick="setFilter('ALL', this)">All ({total_count:,})</button>
        <button class="f-btn" data-filter="YES" onclick="setFilter('YES', this)">Live in Acumatica ({completed_count:,})</button>
        <button class="f-btn" data-filter="NO" onclick="setFilter('NO', this)">Ready to Push ({pending_push_count:,})</button>
        <button class="f-btn" data-filter="PENDING_REVIEW" onclick="setFilter('PENDING_REVIEW', this)">Pending Review ({pending_review_count:,})</button>
        <button class="f-btn" data-filter="P1" onclick="setFilter('P1', this)">P1 Critical ({code_counts.get("P1", 0):,})</button>
        <button class="f-btn" data-filter="P2" onclick="setFilter('P2', this)">P2 Managed ({code_counts.get("P2", 0):,})</button>
        <button class="f-btn" data-filter="P3" onclick="setFilter('P3', this)">P3 Demand ({code_counts.get("P3", 0):,})</button>
        <button class="f-btn" data-filter="HOLD" onclick="setFilter('HOLD', this)">HOLD Review ({code_counts.get("HOLD", 0):,})</button>
      </div>

      <div style="display:flex; align-items:center; gap:12px;">
        <button class="dl-btn" onclick="downloadAcumatica('FILTERED')"
          title="Download currently filtered rows as Acumatica bulk import CSV">
          ⬇ Export Acumatica Import CSV
        </button>
        <div class="search-box">
          <svg class="search-icon-svg" width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
          <input type="text" id="searchInput" placeholder="Type part #, description, or class to search..." oninput="onSearch()">
        </div>
      </div>
    </div>

    <!-- Data Table with Column Header Sorting -->
    <div class="table-card">
      <table>
        <thead>
          <tr>
            <th onclick="sortTable(0)" id="th-0">Part Number <span class="sort-icon">▲</span></th>
            <th onclick="sortTable(1)" id="th-1">Description <span class="sort-icon"></span></th>
            <th onclick="sortTable(2)" id="th-2">Item Class <span class="sort-icon"></span></th>
            <th onclick="sortTable(5)" id="th-5">Cycle Transition <span class="sort-icon"></span></th>
            <th onclick="sortTable(6)" id="th-6">Acumatica Sync <span class="sort-icon"></span></th>
            <th>Decision Rationale / Notes</th>
            <th onclick="sortTable(12)" id="th-12" style="text-align:right;">On Hand Qty <span class="sort-icon"></span></th>
            <th onclick="sortTable(14)" id="th-14" style="text-align:right;">Current ROP <span class="sort-icon"></span></th>
            <th onclick="sortTable(15)" id="th-15" style="text-align:right;">Target ROP <span class="sort-icon"></span></th>
            <th onclick="sortTable(13)" id="th-13" style="text-align:right;">Stock Value ($) <span class="sort-icon"></span></th>
          </tr>
        </thead>
        <tbody id="tableBody"></tbody>
      </table>

      <div class="pagination-bar">
        <div id="pgInfo">Showing 1 to 50</div>
        <div style="display:flex; gap:10px;">
          <button class="pg-btn" id="prevBtn" onclick="changePage(-1)">Previous</button>
          <button class="pg-btn" id="nextBtn" onclick="changePage(1)">Next</button>
        </div>
      </div>
    </div>
    </div> <!-- /tracker-view -->

    <!-- ═══════════════ CATALOG VIEW (hidden by default) ═══════════════ -->
    <div id="catalog-view" style="display:none;">

      <div style="display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:22px;">
        <div>
          <h2 style="font-size:1.4rem; font-weight:800; margin-bottom:4px;">Full Parts Catalog</h2>
          <p style="color:var(--text-sub); font-size:0.9rem;">All {c_total:,} live SKUs from Acumatica, enriched with NetStock and email evidence. Updated: <strong>{as_of}</strong></p>
        </div>
        <button class="dl-btn" onclick="downloadCatalog()" title="Export filtered rows as Acumatica bulk import CSV">
          &#x2B07; Acumatica Import CSV
        </button>
      </div>

      <!-- Catalog KPI Cards -->
      <div class="cat-kpi-grid">
        <div class="cat-kpi">
          <div class="lbl">Total Master SKUs</div>
          <div class="num" style="color:#38bdf8;">{c_total:,}</div>
        </div>
        <div class="cat-kpi">
          <div class="lbl">With Email Evidence</div>
          <div class="num" style="color:#10b981;">{c_email:,}</div>
        </div>
        <div class="cat-kpi">
          <div class="lbl">Stockout (0 Qty)</div>
          <div class="num" style="color:#ef4444;">{c_stockedout:,}</div>
        </div>
        <div class="cat-kpi">
          <div class="lbl">Unreviewed SKUs</div>
          <div class="num" style="color:#64748b;">{c_nodecision:,}</div>
        </div>
      </div>

      <!-- Catalog Filters -->
      <div class="controls-row" style="margin-bottom:18px;">
        <div class="filter-buttons" id="cat-filter-buttons">
          <button class="f-btn active" data-cfilter="ALL"    onclick="setCatFilter('ALL',this)">All ({c_total:,})</button>
          <button class="f-btn" data-cfilter="P1"     onclick="setCatFilter('P1',this)">P1 Critical ({cycle_counts.get('P1',0):,})</button>
          <button class="f-btn" data-cfilter="P2"     onclick="setCatFilter('P2',this)">P2 Managed ({cycle_counts.get('P2',0):,})</button>
          <button class="f-btn" data-cfilter="P3"     onclick="setCatFilter('P3',this)">P3 Demand ({cycle_counts.get('P3',0):,})</button>
          <button class="f-btn" data-cfilter="C"      onclick="setCatFilter('C',this)">Legacy C ({cycle_counts.get('C',0):,})</button>
          <button class="f-btn" data-cfilter="A"      onclick="setCatFilter('A',this)">Legacy A ({cycle_counts.get('A',0):,})</button>
          <button class="f-btn" data-cfilter="U"      onclick="setCatFilter('U',this)">U Superseded ({cycle_counts.get('U',0):,})</button>
          <button class="f-btn" data-cfilter="EMAIL"  onclick="setCatFilter('EMAIL',this)">Email Evidence ({c_email:,})</button>
          <button class="f-btn" data-cfilter="SOUT"   onclick="setCatFilter('SOUT',this)">Stockout ({c_stockedout:,})</button>
          <button class="f-btn" data-cfilter="EXCESS" onclick="setCatFilter('EXCESS',this)">Excess Stock ({c_excess:,})</button>
          <button class="f-btn" data-cfilter="NODEC"  onclick="setCatFilter('NODEC',this)">Unreviewed ({c_nodecision:,})</button>
        </div>
        <div class="search-box">
          <svg class="search-icon-svg" width="18" height="18" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path></svg>
          <input type="text" id="catSearch" placeholder="Part #, description, or supplier..." oninput="onCatSearch()">
        </div>
      </div>

      <!-- Catalog Table -->
      <div class="table-card">
        <table>
          <thead>
            <tr>
              <th onclick="sortCat(0)" id="cth-0">Part # <span class="sort-icon">&#x25B2;</span></th>
              <th onclick="sortCat(1)" id="cth-1">Description <span class="sort-icon"></span></th>
              <th onclick="sortCat(2)" id="cth-2">Supplier <span class="sort-icon"></span></th>
              <th onclick="sortCat(3)" id="cth-3">Item Class <span class="sort-icon"></span></th>
              <th onclick="sortCat(4)" id="cth-4">Live Cycle <span class="sort-icon"></span></th>
              <th onclick="sortCat(5)" id="cth-5">NetStock Class <span class="sort-icon"></span></th>
              <th onclick="sortCat(6)" id="cth-6">Stock Status <span class="sort-icon"></span></th>
              <th onclick="sortCat(7)" id="cth-7" style="text-align:right;">On Hand Qty <span class="sort-icon"></span></th>
              <th onclick="sortCat(8)" id="cth-8" style="text-align:right;">Stock Value ($) <span class="sort-icon"></span></th>
              <th onclick="sortCat(9)" id="cth-9" style="text-align:right;">Avg Mo Forecast <span class="sort-icon"></span></th>
              <th onclick="sortCat(10)" id="cth-10" style="text-align:right;">Annual Hits <span class="sort-icon"></span></th>
              <th onclick="sortCat(11)" id="cth-11" style="text-align:right;">Lead Time (Days) <span class="sort-icon"></span></th>
              <th onclick="sortCat(12)" id="cth-12">Email Signals <span class="sort-icon"></span></th>
              <th onclick="sortCat(18)" id="cth-18">Target Reclass <span class="sort-icon"></span></th>
            </tr>
          </thead>
          <tbody id="catTableBody"></tbody>
        </table>
        <div class="pagination-bar">
          <div id="catPgInfo">Showing 1 to 100</div>
          <div style="display:flex; gap:10px;">
            <button class="pg-btn" id="catPrevBtn" onclick="changeCatPage(-1)">Previous</button>
            <button class="pg-btn" id="catNextBtn" onclick="changeCatPage(1)">Next</button>
          </div>
        </div>
      </div>
    </div> <!-- /catalog-view -->

  </div><!-- /wrap -->


  <script>
    const DATA = {data_json};

    let currentFilter = 'ALL';
    let searchQuery = '';
    let currentPage = 1;
    const pageSize = 50;
    let sortCol = 0; // default Part Number
    let sortAsc = true;
    let filteredData = [...DATA];

    document.addEventListener('DOMContentLoaded', () => {{
      // Status Bar Chart
      new Chart(document.getElementById('statusChart').getContext('2d'), {{
        type: 'bar',
        data: {{
          labels: ['1. Live in Acumatica', '2. Ready to Push', '3. Pending Review'],
          datasets: [{{
            data: [{completed_count}, {pending_push_count}, {pending_review_count}],
            backgroundColor: ['#10b981', '#f59e0b', '#a855f7'],
            borderRadius: 8
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{ legend: {{ display: false }} }},
          scales: {{
            x: {{ grid: {{ display: false }}, ticks: {{ color: '#94a3b8', font: {{ family: 'Inter', size: 12, weight: '600' }} }} }},
            y: {{ grid: {{ color: 'rgba(255, 255, 255, 0.06)' }}, ticks: {{ color: '#94a3b8' }} }}
          }}
        }}
      }});

      // Target Tier Doughnut Chart
      new Chart(document.getElementById('tierChart').getContext('2d'), {{
        type: 'doughnut',
        data: {{
          labels: ['P1 Critical Spares', 'P2 Managed Stock', 'P3 Buy on Demand', 'HOLD Review'],
          datasets: [{{
            data: [{code_counts.get("P1", 0)}, {code_counts.get("P2", 0)}, {code_counts.get("P3", 0)}, {code_counts.get("HOLD", 0)}],
            backgroundColor: ['#ef4444', '#38bdf8', '#94a3b8', '#a855f7'],
            borderColor: '#131b2e',
            borderWidth: 3
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ position: 'right', labels: {{ color: '#ffffff', font: {{ family: 'Inter', size: 12, weight: '600' }} }} }}
          }},
          cutout: '65%'
        }}
      }});
    }});

    function applyFilters() {{
      filteredData = DATA.filter(r => {{
        const [part, desc, iclass, supp, from, to, status, rword, rdetail] = r;
        
        let passFilter = true;
        if (currentFilter === 'YES') passFilter = (status === 'YES');
        else if (currentFilter === 'NO') passFilter = (status === 'NO');
        else if (currentFilter === 'PENDING_REVIEW') passFilter = (status === 'PENDING_REVIEW');
        else if (['P1','P2','P3','HOLD'].includes(currentFilter)) passFilter = (to === currentFilter);

        if (!passFilter) return false;

        if (searchQuery) {{
          const q = searchQuery.toLowerCase();
          return part.toLowerCase().includes(q) || desc.toLowerCase().includes(q) || iclass.toLowerCase().includes(q) || rdetail.toLowerCase().includes(q);
        }}

        return true;
      }});

      // Apply Column Sorting
      filteredData.sort((a, b) => {{
        let valA = a[sortCol];
        let valB = b[sortCol];

        if (typeof valA === 'number' && typeof valB === 'number') {{
          return sortAsc ? valA - valB : valB - valA;
        }}

        valA = (valA || '').toString().toLowerCase();
        valB = (valB || '').toString().toLowerCase();

        if (valA < valB) return sortAsc ? -1 : 1;
        if (valA > valB) return sortAsc ? 1 : -1;
        return 0;
      }});

      currentPage = 1;
      renderTable();
    }}

    function setFilter(filter, btn) {{
      currentFilter = filter;
      document.querySelectorAll('.f-btn').forEach(b => b.classList.remove('active'));
      if (btn) btn.classList.add('active');
      applyFilters();
    }}

    function setFilterByCard(filter) {{
      const btn = document.querySelector(`.f-btn[data-filter="${{filter}}"]`);
      setFilter(filter, btn);
    }}

    function sortTable(colIdx) {{
      if (sortCol === colIdx) {{
        sortAsc = !sortAsc;
      }} else {{
        sortCol = colIdx;
        sortAsc = true;
      }}

      // Update header indicators
      [0, 1, 2, 5, 6, 12, 13, 14, 15].forEach(idx => {{
        const th = document.getElementById(`th-${{idx}}`);
        if (th) {{
          const iconSpan = th.querySelector('.sort-icon');
          if (idx === sortCol) {{
            th.classList.add('sort-active');
            iconSpan.innerText = sortAsc ? '▲' : '▼';
          }} else {{
            th.classList.remove('sort-active');
            iconSpan.innerText = '';
          }}
        }}
      }});

      applyFilters();
    }}

    function onSearch() {{
      searchQuery = document.getElementById('searchInput').value.trim();
      applyFilters();
    }}

    function renderTable() {{
      const tbody = document.getElementById('tableBody');
      tbody.innerHTML = '';

      const total = filteredData.length;
      const startIdx = (currentPage - 1) * pageSize;
      const endIdx = Math.min(startIdx + pageSize, total);
      const pageItems = filteredData.slice(startIdx, endIdx);

      if (pageItems.length === 0) {{
        tbody.innerHTML = `<tr><td colspan="10" style="text-align:center; padding: 40px; color: var(--text-sub);">No parts match your filter or search query.</td></tr>`;
        document.getElementById('pgInfo').innerText = `Showing 0 of 0 parts`;
        document.getElementById('prevBtn').disabled = true;
        document.getElementById('nextBtn').disabled = true;
        return;
      }}

      pageItems.forEach(r => {{
        const [part, desc, iclass, supp, from, to, status, rword, rdetail, appby, date, sheet, onhand, val, rop, setRop] = r;

        let statusPill = '';
        if (status === 'YES') statusPill = '<span class="pill pill-done">● ACTIVE (LIVE)</span>';
        else if (status === 'NO') statusPill = '<span class="pill pill-push">● READY TO PUSH</span>';
        else statusPill = '<span class="pill pill-review">● PENDING REVIEW</span>';

        let codePill = 'code-p3';
        if (to === 'P1') codePill = 'code-p1';
        else if (to === 'P2') codePill = 'code-p2';
        else if (to === 'HOLD') codePill = 'code-hold';

        const isStarred = iclass.endsWith('*');
        const classDisplay = isStarred 
          ? `<span class="p-class-starred" title="Classified by Ajith Srikanth">${{iclass}}</span>`
          : `<span style="color:#cbd5e1;">${{iclass || '-'}}</span>`;

        // Reorder point display — highlight if SetReorderTo differs from current ROP
        const ropDisplay = (rop === 0 || rop === null) ? '<span style="color:#475569;">—</span>' : `<span style="font-variant-numeric:tabular-nums;">${{rop}}</span>`;
        const ropChanged = setRop !== null && setRop !== rop;
        const setRopDisplay = setRop === null
          ? '<span style="color:#475569;">—</span>'
          : ropChanged
            ? `<span style="color:#f59e0b; font-weight:700; font-variant-numeric:tabular-nums;">${{setRop}} ▲</span>`
            : `<span style="color:#64748b; font-variant-numeric:tabular-nums;">${{setRop}}</span>`;

        // On-hand: red if zero, green if above ROP
        const onhandColor = onhand === 0 ? '#ef4444' : (rop > 0 && onhand >= rop ? '#10b981' : '#f1f5f9');

        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><span class="p-code">${{part}}</span></td>
          <td><div class="p-desc">${{desc || '-'}}</div></td>
          <td>${{classDisplay}}</td>
          <td><span class="pill-code ${{codePill}}">${{from}} &rarr; ${{to}}</span></td>
          <td>${{statusPill}}</td>
          <td style="color:#cbd5e1; max-width:300px; font-size:0.85rem;">${{rdetail}}</td>
          <td style="text-align:right; font-weight:700; color:${{onhandColor}}; font-variant-numeric:tabular-nums;">${{onhand}}</td>
          <td style="text-align:right; font-variant-numeric:tabular-nums;">${{ropDisplay}}</td>
          <td style="text-align:right;">${{setRopDisplay}}</td>
          <td style="text-align:right; font-weight:700; font-variant-numeric: tabular-nums;">$${{val.toLocaleString('en-US', {{minimumFractionDigits: 2, maximumFractionDigits: 2}})}}</td>
        `;

        tbody.appendChild(tr);
      }});

      document.getElementById('pgInfo').innerText = `Showing ${{startIdx + 1}} to ${{endIdx}} of ${{total.toLocaleString()}} parts`;
      document.getElementById('prevBtn').disabled = (currentPage === 1);
      document.getElementById('nextBtn').disabled = (endIdx >= total);
    }}

    function changePage(delta) {{
      currentPage += delta;
      renderTable();
    }}

    // ---- Acumatica Bulk Import Download ----------------------------------------
    // Exports in the exact format Acumatica expects: Inventory ID, PI Cycle, Item Class
    // mode = 'FILTERED' (current filteredData) or 'NO' (all Ready to Push rows)
    function downloadAcumatica(mode) {{
      let rows;
      if (mode === 'NO') {{
        rows = DATA.filter(r => r[6] === 'NO');
      }} else {{
        rows = filteredData;
      }}

      if (rows.length === 0) {{
        alert('No parts match the current filter — nothing to download.');
        return;
      }}

      // Exclude HOLD rows — they have no settled target code
      const exportRows = rows.filter(r => r[5] !== 'HOLD');
      const skipped = rows.length - exportRows.length;

      if (exportRows.length === 0) {{
        alert('All filtered rows are HOLD — no settled target code to import.');
        return;
      }}

      // Strip trailing * from ItemClass (it is Ajith's annotation, not an Acumatica value)
      const lines = ['Inventory ID,PI Cycle,Item Class'];
      exportRows.forEach(r => {{
        const part  = (r[0] || '').replace(/,/g, '');
        const cycle = (r[5] || '').replace(/,/g, '');
        const iclass = (r[2] || '').replace(/[*]$/, '').replace(/,/g, '').trim();
        lines.push(`${{part}},${{cycle}},${{iclass}}`);
      }});

      // UTF-8 BOM so Excel opens it correctly without import wizard
      const bom = '\\uFEFF';
      const csvContent = bom + lines.join('\\r\\n');
      const blob = new Blob([csvContent], {{ type: 'text/csv;charset=utf-8;' }});
      const url  = URL.createObjectURL(blob);

      const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
      const label = mode === 'NO' ? 'ReadyToPush' : 'Filtered';
      const filename = `VDRS_AcuBulkImport_${{label}}_${{today}}.csv`;

      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      const msg = skipped > 0
        ? `Downloaded ${{exportRows.length}} parts (${{skipped}} HOLD rows skipped — no settled target code).`
        : `Downloaded ${{exportRows.length}} parts.`;
      console.log('[Acumatica Export]', msg);
    }}

    applyFilters();

    // ═══════════════ CATALOG TAB JS ═══════════════
    const CATALOG_DATA = {catalog_json};

    let catFilter = 'ALL';
    let catSearch = '';
    let catPage = 1;
    const catPageSize = 100;
    let catSortCol = 0;
    let catSortAsc = true;
    let catFiltered = [...CATALOG_DATA];

    function switchTab(tab) {{
      document.getElementById('tracker-view').style.display = tab === 'tracker' ? '' : 'none';
      document.getElementById('catalog-view').style.display  = tab === 'catalog'  ? '' : 'none';
      document.getElementById('tab-tracker').classList.toggle('active', tab === 'tracker');
      document.getElementById('tab-catalog').classList.toggle('active', tab === 'catalog');
      if (tab === 'catalog' && catFiltered.length === CATALOG_DATA.length) applyCatFilters();
    }}

    function applyCatFilters() {{
      catFiltered = CATALOG_DATA.filter(r => {{
        const [part,desc,supp,iclass,cycle,nsclass,status,oh,ohv,fc,hits,lt,emails,crit,md,exp,obs,bo,reclass,rtarget] = r;
        let pass = true;
        if      (catFilter === 'P1')    pass = cycle === 'P1';
        else if (catFilter === 'P2')    pass = cycle === 'P2';
        else if (catFilter === 'P3')    pass = cycle === 'P3';
        else if (catFilter === 'C')     pass = cycle === 'C';
        else if (catFilter === 'A')     pass = cycle === 'A';
        else if (catFilter === 'U')     pass = cycle === 'U';
        else if (catFilter === 'EMAIL') pass = emails > 0;
        else if (catFilter === 'SOUT')  pass = status === 'Stocked out';
        else if (catFilter === 'EXCESS')pass = status === 'Excess stock';
        else if (catFilter === 'NODEC') pass = !reclass;
        if (!pass) return false;
        if (catSearch) {{
          const q = catSearch.toLowerCase();
          return part.toLowerCase().includes(q) || desc.toLowerCase().includes(q) || supp.toLowerCase().includes(q) || iclass.toLowerCase().includes(q);
        }}
        return true;
      }});

      catFiltered.sort((a, b) => {{
        let vA = a[catSortCol], vB = b[catSortCol];
        if (typeof vA === 'number' && typeof vB === 'number') return catSortAsc ? vA - vB : vB - vA;
        vA = (vA || '').toString().toLowerCase();
        vB = (vB || '').toString().toLowerCase();
        if (vA < vB) return catSortAsc ? -1 : 1;
        if (vA > vB) return catSortAsc ? 1 : -1;
        return 0;
      }});

      catPage = 1;
      renderCatTable();
    }}

    function setCatFilter(f, btn) {{
      catFilter = f;
      document.querySelectorAll('#cat-filter-buttons .f-btn').forEach(b => b.classList.remove('active'));
      if (btn) btn.classList.add('active');
      applyCatFilters();
    }}

    function onCatSearch() {{
      catSearch = document.getElementById('catSearch').value.trim();
      applyCatFilters();
    }}

    function sortCat(col) {{
      if (catSortCol === col) catSortAsc = !catSortAsc;
      else {{ catSortCol = col; catSortAsc = true; }}
      [0,1,2,3,4,5,6,7,8,9,10,11,12,18].forEach(idx => {{
        const th = document.getElementById(`cth-${{idx}}`);
        if (!th) return;
        const sp = th.querySelector('.sort-icon');
        if (idx === catSortCol) {{ th.classList.add('sort-active'); sp.innerText = catSortAsc ? '\u25B2' : '\u25BC'; }}
        else {{ th.classList.remove('sort-active'); sp.innerText = ''; }}
      }});
      applyCatFilters();
    }}

    function renderCatTable() {{
      const tbody = document.getElementById('catTableBody');
      tbody.innerHTML = '';
      const total = catFiltered.length;
      const start = (catPage - 1) * catPageSize;
      const end   = Math.min(start + catPageSize, total);
      const page  = catFiltered.slice(start, end);

      if (!page.length) {{
        tbody.innerHTML = '<tr><td colspan="14" style="text-align:center;padding:40px;color:var(--text-sub);">No parts match.</td></tr>';
        document.getElementById('catPgInfo').innerText = 'Showing 0 of 0 parts';
        document.getElementById('catPrevBtn').disabled = true;
        document.getElementById('catNextBtn').disabled = true;
        return;
      }}

      page.forEach(r => {{
        const [part,desc,supp,iclass,cycle,nsclass,status,oh,ohv,fc,hits,lt,emails,crit,md,exp,obs,bo,reclass,rtarget] = r;

        // Cycle pill
        let cycleCls = 'code-p3';
        if (cycle==='P1') cycleCls='code-p1';
        else if (cycle==='P2') cycleCls='code-p2';
        else if (cycle==='C'||cycle==='A') cycleCls='code-hold';
        const cyclePill = `<span class="pill-code ${{cycleCls}}">${{cycle||'-'}}</span>`;

        // Status badge
        let statusColor = '#94a3b8';
        if (status==='Stocked out') statusColor='#ef4444';
        else if (status==='Excess stock') statusColor='#f59e0b';
        else if (status==='Ok') statusColor='#10b981';
        const statusBadge = status ? `<span style="font-size:0.78rem;font-weight:700;color:${{statusColor}};">${{status}}</span>` : '-';

        // Email signals
        let emailBadges = '';
        if (emails > 0) {{
          emailBadges += `<span class="badge badge-blue" title="Total emails">&#x2709; ${{emails}}</span>`;
          if (crit > 0)   emailBadges += `<span class="badge badge-red"   title="Critical mentions">&#x1F534; ${{crit}}</span>`;
          if (md > 0)     emailBadges += `<span class="badge badge-amber"  title="Machine down">&#x26A0; ${{md}}</span>`;
          if (exp > 0)    emailBadges += `<span class="badge badge-amber"  title="Expedite requests">&#x1F69A; ${{exp}}</span>`;
          if (obs > 0)    emailBadges += `<span class="badge badge-gray"   title="Obsolete mentions">&#x274C; ${{obs}}</span>`;
          if (bo > 0)     emailBadges += `<span class="badge badge-purple" title="Backorder mentions">&#x23F3; ${{bo}}</span>`;
        }}

        // Reclass badge
        let reclassBadge = '<span style="color:#475569;">&#x2014;</span>';
        if (reclass==='YES')            reclassBadge = `<span class="reclass-done" title="Target: ${{rtarget}}">&#x2705; ${{rtarget}}</span>`;
        else if (reclass==='NO')        reclassBadge = `<span class="reclass-push" title="Target: ${{rtarget}}">&#x23F3; ${{rtarget}}</span>`;
        else if (reclass==='PENDING_REVIEW') reclassBadge = `<span class="reclass-review" title="Target: ${{rtarget}}">&#x1F50D; ${{rtarget}}</span>`;

        const isStarred = iclass.endsWith('*');
        const classDisplay = isStarred
          ? `<span class="p-class-starred" title="Classified by Ajith Srikanth">${{iclass}}</span>`
          : `<span style="color:#cbd5e1;">${{iclass || '—'}}</span>`;

        const escDesc = (desc || '').replace(/"/g, '&quot;');
        const escSupp = (supp || '').replace(/"/g, '&quot;');

        const ohColor = oh === 0 ? '#ef4444' : '#f1f5f9';
        const ohvFmt = '$' + ohv.toLocaleString('en-US', {{maximumFractionDigits: 0}});
        const dash = '<span style="color:#475569;">&#x2014;</span>';
        const fcDisp = fc  > 0 ? fc.toFixed(2) : dash;
        const ltDisp = lt  > 0 ? (lt + 'd')    : dash;
        const emDisp = emailBadges || dash;

        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><span class="p-code">${{part}}</span></td>
          <td><div class="p-desc" title="${{escDesc}}">${{desc||'—'}}</div></td>
          <td><div class="p-desc" style="max-width:200px; color:#94a3b8; font-size:0.83rem;" title="${{escSupp}}">${{supp||'—'}}</div></td>
          <td>${{classDisplay}}</td>
          <td>${{cyclePill}}</td>
          <td style="font-size:0.8rem;color:#94a3b8;">${{nsclass||'—'}}</td>
          <td>${{statusBadge}}</td>
          <td style="text-align:right;font-weight:700;color:${{ohColor}};font-variant-numeric:tabular-nums;">${{oh.toLocaleString()}}</td>
          <td style="text-align:right;font-variant-numeric:tabular-nums;">${{ohvFmt}}</td>
          <td style="text-align:right;font-variant-numeric:tabular-nums;">${{fcDisp}}</td>
          <td style="text-align:right;">${{hits||0}}</td>
          <td style="text-align:right;">${{ltDisp}}</td>
          <td>${{emDisp}}</td>
          <td>${{reclassBadge}}</td>
        `;
        tbody.appendChild(tr);
      }});

      document.getElementById('catPgInfo').innerText = `Showing ${{start+1}} to ${{end}} of ${{total.toLocaleString()}} parts`;
      document.getElementById('catPrevBtn').disabled = catPage === 1;
      document.getElementById('catNextBtn').disabled = end >= total;
    }}

    function changeCatPage(delta) {{
      catPage += delta;
      renderCatTable();
    }}

    function downloadCatalog() {{
      if (!catFiltered.length) {{ alert('Nothing to download.'); return; }}
      const lines = ['Inventory ID,PI Cycle,Item Class'];
      catFiltered.forEach(r => {{
        const part  = (r[0] || '').replace(/,/g,'');
        const cycle = (r[4] || '').replace(/,/g,'');
        const ic    = (r[3] || '').replace(/[*]$/,'').replace(/,/g,'').trim();
        if (part && cycle) lines.push(`${{part}},${{cycle}},${{ic}}`);
      }});
      const bom = '\\uFEFF';
      const blob = new Blob([bom + lines.join('\\r\\n')], {{type:'text/csv;charset=utf-8;'}});
      const url  = URL.createObjectURL(blob);
      const today = new Date().toISOString().slice(0,10).replace(/-/g,'');
      const a = document.createElement('a');
      a.href = url; a.download = `VDRS_Catalog_Export_${{today}}.csv`;
      document.body.appendChild(a); a.click();
      document.body.removeChild(a); URL.revokeObjectURL(url);
    }}

    applyCatFilters();
  </script>
</body>
</html>
"""
    return html


def git_push_tracker(commit_msg=None):
    """Commit and push docs/index.html in vdrs-cycle-tracker repo to update GitHub Pages."""
    import subprocess
    repo_dir = REPO_ROOT
    if not repo_dir.exists():
        print("vdrs-cycle-tracker directory not found, skipping git push")
        return False
    try:
        # Fetch remote updates first
        subprocess.run(["git", "-C", str(repo_dir), "fetch", "origin", "main"], capture_output=True, check=False)
        # Stage generated outputs
        subprocess.run(["git", "-C", str(repo_dir), "add", "docs/index.html"], check=False)
        data_dir = repo_dir / "data"
        if data_dir.exists():
            subprocess.run(["git", "-C", str(repo_dir), "add", "data"], check=False)

        status = subprocess.run(["git", "-C", str(repo_dir), "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip():
            print("vdrs-cycle-tracker working tree clean, no git commit needed.")
        else:
            if not commit_msg:
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                commit_msg = f"auto: update cycle classification tracker dashboard ({now_str})"
            subprocess.run(["git", "-C", str(repo_dir), "commit", "-m", commit_msg], check=True)
            print(f"Committed changes: {commit_msg}")

        # Attempt push. If rejected due to upstream cloud runs, merge remote using ours strategy and retry push
        push_res = subprocess.run(["git", "-C", str(repo_dir), "push", "origin", "main"], capture_output=True, text=True)
        if push_res.returncode != 0:
            print("Push was rejected; reconciling remote changes with -X ours...")
            subprocess.run(["git", "-C", str(repo_dir), "merge", "origin/main", "-X", "ours", "-m", "merge(cloud): reconcile with cloud run"], capture_output=True, check=False)
            subprocess.run(["git", "-C", str(repo_dir), "push", "origin", "main"], check=True)

        print("Pushed updated dashboard to GitHub Pages (origin/main).")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Git command failed: {e}")
        return False
    except Exception as e:
        print(f"Git push warning: {e}")
        return False


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="pull live PICycle from Acumatica before building")
    ap.add_argument("--push", action="store_true", help="commit and push to GitHub Pages if changed")
    args = ap.parse_args()

    if args.live:
        print("--- Pulling live PICycle from Acumatica ---")
        try:
            sys.path.insert(0, str(ROOT / "30_Tool_Acumatica_Live_Pull"))
            from acu_pull import pull_gi
            st = pull_gi("NSstock")
            st["part"] = st["InventoryID"].astype(str).str.strip()
            reorder_col = "INItemSite_Formula28047613d714e711bf70a41731d920ce"
            out = st[["part", "PICycle", reorder_col]].copy()
            out.columns = ["part", "live_cycle", "reorder_point"]
            out["live_cycle"] = out["live_cycle"].astype(str).str.strip()
            out = out.sort_values("part").drop_duplicates("part")
            out["pulled_at"] = datetime.now().isoformat(timespec="seconds")
            LIVE_CYCLE_PATH.parent.mkdir(parents=True, exist_ok=True)
            out.to_csv(LIVE_CYCLE_PATH, index=False)
            print(f"Pulled {len(out)} parts -> {LIVE_CYCLE_PATH}")
        except Exception as e:
            print(f"Live pull warning ({e}), proceeding with existing {LIVE_CYCLE_PATH}")

    df = fetch_data()
    catalog_df = fetch_full_catalog(df)
    html_content = generate_html(df, catalog_df)
    for p in OUT_PATHS:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html_content, encoding="utf-8")
        print(f"Wrote dashboard HTML -> {p}")

    if args.push:
        completed = (df["AppliedInAcumatica"] == "YES").sum()
        pending = (df["AppliedInAcumatica"] == "NO").sum()
        msg = f"feat(dashboard): update live counts ({completed:,} live in Acumatica, {pending:,} ready to push) - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        git_push_tracker(msg)


if __name__ == "__main__":
    main()
