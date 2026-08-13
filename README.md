# VDRS Cycle Tracker

Internal dashboard: parts whose Acumatica cycle code (PICycle) changed after service-team
review, and whether the change is live in Acumatica yet.

Data lives in `dbo.CycleChangeLog` (PowerAppsDatabase). This repo publishes a static summary
of that table via GitHub Pages (`docs/index.html`). Regenerate with
`24_Project_Cycle_Reclass_Chris/04_Change_Tracker/tools/` in the Inventory Analyst folder.
