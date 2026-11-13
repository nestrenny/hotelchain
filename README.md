# Aurelian Collection – Enterprise Hospitality Intelligence Platform (local prototype)

A working, end-to-end prototype of the platform specified in
[docs/Luxury Hotel Chain – Enterprise Data Analytics and AI Control Tower - Writing-block.md](docs/Luxury%20Hotel%20Chain%20%E2%80%93%20Enterprise%20Data%20Analytics%20and%20AI%20Control%20Tower%20-%20Writing-block.md):
source ingestion, a Bronze / Silver / Gold medallion lakehouse, eight enterprise dashboards and
the AI Control Tower, for a fictional six-property luxury chain.

It runs entirely on this machine on synthetic data. Each local component stands in for a named
Azure / Fabric service, so the design ports across rather than being thrown away (see the mapping
below). Requirement-by-requirement coverage is in [docs/traceability.md](docs/traceability.md).

## Run it

Requires Python 3.11+ (built and tested on 3.13). From this folder, in PowerShell:

```powershell
.\run.ps1            # first run: creates .venv, installs packages, builds the lakehouse (about 2 minutes), starts the app
```

Then open <http://localhost:8000>. Later runs skip what is already built. Individual steps:

```powershell
.\.venv\Scripts\python.exe -m hip.pipelines.run all        # generate -> bronze -> silver -> gold -> quality -> ai
.\.venv\Scripts\python.exe -m hip.pipelines.run bronze     # one stage: generate | bronze | silver | gold | quality | ai
.\.venv\Scripts\python.exe -m pytest -q                    # 25 acceptance tests
.\.venv\Scripts\python.exe -m uvicorn hip.api.app:app --port 8000
```

Stop the web app before re-running the pipeline (the lakehouse file allows one writer).

## What is in it

| Layer | Local implementation | Azure / Fabric target |
|---|---|---|
| Source systems | Synthetic extracts in `data/landing/`: two PMS vendors with different schemas, restaurant POS, table booking, park ticketing, retail POS and inventory, spa booking, CRM / loyalty / marketing, ERP | The chain's real PMS, POS, CRM and ERP systems |
| Ingestion | `hip/pipelines/bronze.py`, driven by `config/sources.json`: incremental by watermark, schema evolution, retry, quarantine, ingestion log | Data Factory, Event Hubs, API Management, Functions; secrets in Key Vault |
| Lakehouse | One DuckDB file, `data/lakehouse.duckdb`, with `bronze`, `silver`, `gold`, `ops` and `ai` schemas | Fabric Lakehouse / Warehouse on OneLake |
| Bronze | 29 raw tables, all text, plus source, file, batch, timestamp and row-hash metadata | Bronze Delta tables |
| Silver | `hip/pipelines/silver.py`: typed, de-duplicated, conformed; vendor harmonisation; USD conversion; unified guest identity; SCD2 loyalty history | Fabric notebooks / dataflows |
| Gold | `hip/pipelines/gold.py`: 17 dimensions, 20 facts, 3 marts (daily revenue, executive, Guest 360) | Gold star schema and Power BI semantic model |
| Quality and governance | `hip/pipelines/quality.py`: 20 rules including operations-to-ledger reconciliation; automatic lineage, catalogue, classification and KPI glossary in `ops` | Purview and Fabric governance |
| AI | `hip/ai/`: 10 forecast families (138 series), cancellation, churn, segmentation and lifetime value, campaign conversion, queue prediction; model registry with versions and drift | Azure Machine Learning, Fabric notebooks |
| Prescriptive | `hip/ai/recommend.py`: 15 explainable rules over model output, plus anomaly detectors and alerts | AI Control Tower services |
| Serving | `hip/api/` (FastAPI) and `web/` (hand-built HTML, CSS and SVG charts, no build step) | Power BI reports and the Control Tower app |
| Security | Persona switcher with property and business-vertical filtering enforced in the API | Entra ID, RBAC, Power BI row-level security |
| Workflow | `data/app.db` (SQLite): approvals, alert actions, exports, full audit trail | Approval workflow with audit logging |

### Pages

- **Performance**: Executive, Hotels, Restaurants, Waterpark, Retail, Spa & Leisure, Marketing & Loyalty, Finance.
  Global property, period and comparison filters; actual, budget and forecast drawn distinctly; KPI definitions on
  every tile; AI insights tied to their measure; drill-through from enterprise to property; permission-gated CSV export.
- **AI Control Tower**: Intelligence Overview, Forecasting Workspace (30/60/90 days, intervals, backtest accuracy,
  scenarios), Recommendations (approve, reject, implement, reassign, with history), Alert Management
  (acknowledge, assign, escalate, resolve), Model Monitoring.
- **Platform**: Data & Governance (ingestion monitor, data quality, reconciliation, catalogue and lineage,
  classification, privacy, glossary) and the Audit Trail.

Use the **Signed in as** selector to switch between ten personas (CEO, CFO, regional director, general manager,
revenue manager, F&B director, marketing, read-only finance analyst, data steward). Each sees only its own
properties and verticals, and only authorised roles can approve or export.

## Assumptions

SRS section 17 lists items to confirm before detailed development. For the prototype they are assumed as follows;
all are configuration, in `config/`.

| Item | Assumption |
|---|---|
| Estate | 6 properties, 2 brands (Aurelian Reserve, Aurelian Resorts), 3 regions, 6 local currencies; 2 properties have waterparks. All names are fictional. |
| Source systems | Two fictional PMS vendors (StaySuite, InnKeeper) to prove multi-vendor harmonisation; one system per other domain. |
| History | 1 November 2024 to 12 November 2026 (the "last closed business date", fixed so results are reproducible; override with `HIP_AS_OF`). About 3.5 million source rows. |
| Reporting currency | USD. Local amounts convert at the daily rate on the transaction date (arrival date for room revenue). |
| Financial calendar | Calendar year, monthly periods, monthly budgets pro-rated by day. |
| KPI definitions | As in SRS 8.1, with exclusions written out in `config/kpi_definitions.json`. Revenue is net of discounts and excludes taxes. |
| Guest identity | Matched on normalised e-mail only. Gold holds pseudonymised keys, hashed e-mail and masked names. |
| Refresh | The pipeline is a batch run. Target frequencies per feed are recorded in `config/sources.json` but not scheduled. |

## Known problems planted in the data

So that the quality rules, anomaly detectors and recommendations have real things to find, the generator plants:
a few rows with missing keys or invalid dates; duplicate POS lines; earlier versions of changed reservations;
two days of F&B revenue missing from the Doha ledger; a utilities cost spike at Dubai Palm; a supplier price
increase at Phuket; a discounting spike at a Phuket boutique; slow-moving and under-stocked retail lines;
an under-used spa and a low-margin property (Doha). The tests assert that each one is detected.

## Limits of the prototype

- **Synthetic data.** Model accuracy figures describe generated data and say nothing about accuracy on the chain's real data.
- **No Azure deployment.** Nothing here has been deployed to or tested against Azure, Fabric or Power BI. The mapping above is a design intent.
- **Simulated sign-in.** The persona selector replaces Entra ID. The API enforces scope, but anyone can pick any persona.
- **Batch only.** Near-real-time ingestion (5 to 15 minutes) is modelled by watermarks and configuration, not by a running stream.
- **Forecasts** use calendar, seasonality, event and prior-year features. Hotel forecasts are floored at rooms already on the books, but booking pace is not yet a model feature.
- **Recommendation impact** figures are estimates from stated assumptions (shown on each recommendation), not measured outcomes.
- **Not covered**: SIT / UAT / production environments, CI/CD, disaster recovery, performance testing at production volumes, and guest satisfaction beyond a post-stay survey score.

## Layout

```
config/     properties, source registry, KPI glossary, personas
hip/synth/  synthetic source-system generator
hip/pipelines/  bronze, silver, gold, quality, orchestrator
hip/ai/     forecasting, predictive models, recommendations and alerts
hip/api/    FastAPI app, dashboards, Control Tower, security, workflow
web/        single-page client (no build step)
tests/      acceptance tests
data/       generated: landing files, lakehouse, workflow database, model artefacts
```
