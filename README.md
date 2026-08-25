# VDRS Cycle Classification Tracker

Internal interactive dashboard tracking cycle code reclassifications (`PICycle`) across **9,499 parts**, synchronized directly with Acumatica and the VDRS SQL Server database (`dbo.CycleChangeLog` on `vdrsapps.database.windows.net`).

## Executive Summary Metrics

- **Completed (Live in Acumatica)**: 7,753 parts: Confirmed reclassification decisions active in Acumatica.
- **Pending Push to Acumatica**: 0 parts: All approved batches are live.
- **Pending Review**: 1,741 parts: Open clarification questions and field review hold items.
- **Target Code Tiers**: `P1` Critical Spares, `P2` Managed Stock, `P3` Non-Stock / Buy-on-Demand, `U` Superseded, `O` Obsolete, `HOLD` Field Review.
- **Full Catalog Search**: Instant search across 25,916 SKUs with live codes, stock values, forecast, hits, lead time, and email activity.
- **Chronological Audit Trail**: Full decision dates, approval trail, on-hand units, and stock value carried ($1.97M).

## Live Site

Published automatically via GitHub Pages: `https://ajithvandyk.github.io/vdrs-cycle-tracker/` (from `docs/index.html`).

## Automated Sync Pipeline

The tracker updates automatically via multiple channels:

### 1. GitHub Actions (Cloud Auto-Sync)
- **Schedule**: Every weekday at 7:00 AM EDT and 1:00 PM EDT (`.github/workflows/daily_sync.yml`).
- **Manual Trigger**: "Run workflow" button in GitHub Actions tab.
- **Action**: Pulls live `NSstock` from Acumatica OData, updates change tracking & SQL Server, re-compiles `docs/index.html`, and deploys.

### 2. Local Windows Scheduled Task & 1-Click Runner
- **Scheduled Task**: `VDRS Cycle Tracker Daily Refresh` runs daily at 7:45 AM EDT.
- **1-Click Runner**: `Run_Cycle_Tracker_Update.cmd` in repo root and `24_Project_Cycle_Reclass_Chris/04_Change_Tracker/`.
- **Master Script**: `24_Project_Cycle_Reclass_Chris/04_Change_Tracker/tools/sync_cycle_tracker.py`.
