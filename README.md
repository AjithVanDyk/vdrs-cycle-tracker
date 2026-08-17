# VDRS Cycle Classification Tracker

Internal interactive dashboard tracking cycle code reclassifications (`PICycle`) across **9,494 parts**, connected directly to the VDRS SQL Server database (`dbo.CycleChangeLog` on `vdrsapps.database.windows.net`).

## 📊 Dashboard Features

- **Category-Wise Breakdown Matrix**:
  - **Completed (Live in Acumatica)**: 38 parts — Re-verified decisions live in Acumatica.
  - **Pending Push to Acumatica**: 7,715 parts — Approved decisions ready to be pushed.
  - **Pending Review**: 1,741 parts — Open clarification questions and field review hold items.
- **Target Code Tiers**: `P1` Critical Spares, `P2` Managed Stock, `P3` Non-Stock / Buy-on-Demand, `U` Superseded, `O` Obsolete, `HOLD` Field Review.
- **Interactive Search & Filtering**: Instant search by Part Number, Description, Supplier, Item Class, or Reason detail.
- **Chronological Audit Trail**: Full decision dates, approval trail, on-hand units, and stock value carried ($1.97M).

## 🚀 Live Site

Published via GitHub Pages at: `docs/index.html`.

## 🛠️ Data Pipeline & Tools

Data is managed and regenerated via the scripts in `24_Project_Cycle_Reclass_Chris/04_Change_Tracker/tools/`:
1. `extract_changes.py` — Extracts decisions and open review lists into `changes.csv`.
2. `load_sql.py --apply` — Upserts all 9,494 records into `dbo.CycleChangeLog` on VDRS SQL Server using credentials from `G:\After Sales Team\Van Dyk Tools\config\config.json`.
3. `build_site_data.py` — Queries SQL Server and compiles `docs/index.html`.
