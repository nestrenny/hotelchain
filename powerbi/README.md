# Offline Power BI report

A Power BI Desktop report built on the same data as the web app. It needs no server, gateway or sign-in:
the data is the Parquet files in `data/`, exported from the Gold and AI layers of the local lakehouse.

## Open it

1. Open `Aurelian Hospitality Intelligence.pbip` in Power BI Desktop.
2. Select **Refresh** on the Home ribbon. A Power BI Project holds no data until its first refresh; this one takes a few seconds.
3. To keep a single self-contained file, use **File > Save as** and choose `.pbix`.

If the folder has moved since it was built, either run `python tools/build_powerbi.py` again, or in Power BI Desktop choose
**Transform data > Edit parameters** and set `DataFolder` to the full path of the `data` folder.

## What is in it

| Page | Shows |
|---|---|
| Executive | Revenue, occupancy, ADR, RevPAR, GOP; revenue by day, by vertical and by property |
| Hotels | Occupancy by day, room revenue by booking channel, room categories, ADR and RevPAR by month |
| Restaurants | Covers, sales, spend per guest and food cost, by day, meal period and restaurant |
| Waterpark | Visitors, ticket revenue, capacity utilisation and queue times |
| Retail and Spa | Retail sales and margin by day and category; spa appointments and revenue |
| Marketing | Campaign spend, attributed revenue, return on investment and conversion by channel |
| Finance | Ledger revenue and expenses against budget by month, cost centre and property |
| AI Control Tower | Revenue forecast with its range, recommendations and their estimated impact, alerts |
| Energy and Assets | Energy against expected and forecast, end use by property, contract reminders, expected device failures |

Every page has a Property slicer and a Period slicer; they stay in step across pages.

The model has 28 tables (7 dimensions, 21 facts), 52 relationships and 81 measures. The relationships are drawn in
[docs/Database Schema.drawio](../docs/Database%20Schema.drawio).

## Differences from the web app

- **Period.** The report opens on all dates (November 2024 onwards); the web app opens on the last 30 days. Use the Period slicer to match.
- **Access.** There is no role selector. Anyone who opens the file sees every property and domain.
- **Read-only.** Recommendations and alerts are listed, but approving or rejecting them is only possible in the web app.
- **Budget.** Monthly budgets are spread evenly over the days of each month so they can be compared over any period.
- **Not included:** guest-level tables, the forecasting workspace, model monitoring detail and the data-governance page.

## Rebuild

```powershell
.\.venv\Scripts\python.exe tools\build_powerbi.py --validate
```

This exports the data again and rewrites the model and report from `tools/build_powerbi.py`, where the tables, measures and
pages are defined. `--validate` checks the report files against Microsoft's published schemas (needs internet the first time).
Changes made in Power BI Desktop and saved to this project are overwritten by a rebuild.
