r"""Sync VDRS Cycle Classification Tracker Dashboard.

Entry point for GitHub Actions and cloud/local runners:
1. Pulls live PICycle & ROP from Acumatica (NSstock GI via OData).
2. Updates Azure SQL Server dbo.CycleChangeLog if SQL credentials exist in environment.
3. Compiles docs/index.html with up-to-date live counts, full catalog, and change history.
"""
from datetime import datetime
import os
from pathlib import Path
import sys
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import acu_pull
import db
import build_site_data


def run_sync():
    print("=" * 70)
    print(f"[{datetime.now().isoformat()}] Starting VDRS Cycle Tracker Cloud Sync")
    print("=" * 70)

    # 1. Pull live PICycle from Acumatica
    print("Step 1: Pulling live cycle codes from Acumatica...")
    try:
        st = acu_pull.pull_gi("NSstock")
        st["part"] = st["InventoryID"].astype(str).str.strip()
        reorder_col = "INItemSite_Formula28047613d714e711bf70a41731d920ce"
        live_df = st[["part", "PICycle", reorder_col]].copy()
        live_df.columns = ["part", "live_cycle", "reorder_point"]
        live_df["live_cycle"] = live_df["live_cycle"].astype(str).str.strip()
        live_df = live_df.sort_values("part").drop_duplicates("part")
        live_df["pulled_at"] = datetime.now().isoformat(timespec="seconds")

        live_out = REPO_ROOT / "data" / "live_cycle.csv"
        live_out.parent.mkdir(parents=True, exist_ok=True)
        live_df.to_csv(live_out, index=False)
        print(f"  -> Successfully saved {len(live_df):,} parts to {live_out.name}")
        print(f"  -> Breakdown: {dict(live_df['live_cycle'].value_counts())}")
    except Exception as e:
        print(f"  -> Warning: Acumatica live pull failed: {e}")
        print("  -> Continuing with existing data snapshots if available")

    # 2. Fetch data (SQL Server or changes.csv fallback)
    print("\nStep 2: Fetching change records...")
    df = build_site_data.fetch_data()
    print(f"  -> Total tracking items: {len(df):,}")

    # 3. If SQL Server is available, push live status back to SQL Server
    if db.credentials():
        print("\nStep 3: Synchronizing live status to Azure SQL Server (dbo.CycleChangeLog)...")
        db.update_live_status_sql(df)
    else:
        print("\nStep 3: SQL Server credentials not in environment, skipping direct SQL update")

    # 4. Generate dashboard HTML
    print("\nStep 4: Compiling docs/index.html...")
    catalog_df = build_site_data.fetch_full_catalog(df)
    html_content = build_site_data.generate_html(df, catalog_df)

    out_file = REPO_ROOT / "docs" / "index.html"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(html_content, encoding="utf-8")
    print(f"  -> Successfully compiled dashboard -> {out_file}")

    # Also update changes.csv in data/
    data_changes = REPO_ROOT / "data" / "changes.csv"
    data_changes.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(data_changes, index=False)

    completed = (df["AppliedInAcumatica"] == "YES").sum()
    pending_push = (df["AppliedInAcumatica"] == "NO").sum()
    pending_review = (df["AppliedInAcumatica"] == "PENDING_REVIEW").sum()

    print("\n" + "=" * 70)
    print("SYNC SUCCESSFUL:")
    print(f"  - Completed (Live in Acumatica): {completed:,}")
    print(f"  - Ready to Push to Acumatica:   {pending_push:,}")
    print(f"  - Pending Clarification/Review: {pending_review:,}")
    print("=" * 70)


if __name__ == "__main__":
    run_sync()
