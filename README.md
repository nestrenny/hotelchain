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
.\.venv\Scripts\python.exe -m hip.pipelines.run all        # generate -> bronze -> silver -> gold -> quality -> ai -> energy
.\.venv\Scripts\python.exe -m hip.pipelines.run bronze     # one stage: generate | bronze | silver | gold | quality | ai | energy
.\.venv\Scripts\python.exe -m pytest -q                    # 30 acceptance tests
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

- **Welcome screen**: the app opens on the hero photograph with two solid cards laid over it: the full menu on the left, and a welcome panel
  with four live headline figures. It lists only the pages the signed-in role can use; the brand mark in the side menu returns to it.
- **Performance**: Executive, Hotels, Restaurants, Waterpark, Retail, Spa & Leisure, Marketing & Loyalty, Finance.
  Global property, period and comparison filters; actual, budget and forecast drawn distinctly; KPI definitions on
  every tile; AI insights tied to their measure; drill-through from enterprise to property; permission-gated CSV export.
- **AI Control Tower**: Intelligence Overview, Forecasting Workspace (30/60/90 days, intervals, backtest accuracy,
  scenarios), Recommendations (approve, reject, implement, reassign, with history), Alert Management
  (acknowledge, assign, escalate, resolve), Model Monitoring.
- **Energy & Assets** (in the AI Control Tower menu): one page of AI predictions for engineering. Energy use against an expected baseline with a 30-day forecast,
  energy anomalies, vacant-room waste, peak-demand risk and water-leak detection; device failure prediction, estimated life left, spares and technician workload;
  and reminders for contracts, warranties and devices due for replacement. See [Energy & Assets](#energy--assets) below.
- **Platform**: Data & Governance (ingestion monitor, data quality, reconciliation, catalogue and lineage,
  classification, privacy, glossary) and the Audit Trail.

Use the **Signed in as** selector to switch between eleven personas (CEO, CFO, regional director, general manager,
revenue manager, F&B director, marketing, read-only finance analyst, engineering director, data steward). Each sees only its own
properties and verticals, and only authorised roles can approve or export.

## Energy & Assets

An extension beyond the seven SRS domains, added as its own pipeline stage (`python -m hip.pipelines.run energy`) so the rest of the estate is unchanged.

| Area | What the page predicts | How |
|---|---|---|
| Energy management | Expected energy use for the occupancy and weather, a 30-day forecast of use, cost and carbon, and properties running above expectation | Gradient boosting on occupancy, degree-days and calendar; the forecast reuses the hotel occupancy forecast |
| Energy waste | Energy used by vacant rooms relative to occupied ones; peak load against contracted supply capacity; probable water leaks | Ratio against a target; forecast peak; robust z-score on overnight water flow |
| Predictive maintenance | Probability that each of 6,190 devices fails in the next 30 days, with its warning signs and a recommended action | Gradient boosting classifier on age, runtime, fault codes, power-draw drift and service history |
| Lifecycle | Estimated life left, repair-versus-replace, a replacement capital forecast, and spares and technician hours for the next 30 days | Engineering rules on telemetry and repair history; sum of failure probabilities |
| Contracts and warranties | Contracts lapsed or ending within 120 days, notice deadlines, warranties about to expire, and the predicted repair cost if cover lapses | Date rules joined to the failure predictions |

Source feeds (building-management meters, weather, device register, IoT telemetry, maintenance work orders, contract register), device types, tariffs and carbon factors are in
`config/energy.json`. Planted problems that the tests assert are found: a chiller fault at Dubai Palm, vacant rooms not set back at Doha, a water leak at Phuket, and three lapsed contracts.

Not yet done: these models and findings live on this page only and are not in the central Model Monitoring, Recommendations or Alerts pages, and the page is not covered by the user guide or the deck.

## Documents

| Document | What it is |
|---|---|
| [docs/Dashboard User Guide.docx](docs/Dashboard%20User%20Guide.docx) | 77-page guide to every dashboard, card, graph and table: what each shows, how it is calculated and how to read it. Also covers the AI Control Tower, the governance page, the KPI definitions and the roles. |
| [docs/Leadership Briefing.pptx](docs/Leadership%20Briefing.pptx) | 14-slide deck with speaker notes for the leadership team: what was built, what it shows (including energy and asset predictions), its limits, the path to production and the decisions needed. |
| [powerbi/](powerbi/README.md) | Offline Power BI report on the same data: nine pages, 28 tables, 81 measures. Open the `.pbip` file in Power BI Desktop and select Refresh. |
| [docs/Database Schema.drawio](docs/Database%20Schema.drawio) | Logical schema of the reporting database for draw.io (diagrams.net): a fact-by-dimension matrix, then one entity-relationship page per subject area with keys and relationships. |
| [docs/Platform Explainer.mp4](docs/Platform%20Explainer.mp4) | 4-minute narrated guided tour of the running app: seven hotel-industry problems and the screens that solve each one. The camera zooms to and highlights each area as it is described. Subtitles are in the matching `.srt` file. |
| [docs/traceability.md](docs/traceability.md) | Requirement-by-requirement status against the SRS. |
| [docs/Luxury Hotel Chain – Enterprise Data Analytics and AI Control Tower - Writing-block.md](docs/Luxury%20Hotel%20Chain%20%E2%80%93%20Enterprise%20Data%20Analytics%20and%20AI%20Control%20Tower%20-%20Writing-block.md) | The source requirements specification (SRS). |

The guide, the deck and the video are generated from the running app, so their screenshots and figures match the platform
(synthetic data, business date 12 November 2026). To regenerate them after a change, with the app running:

```powershell
.\.venv\Scripts\python.exe -m pip install python-docx python-pptx playwright Pillow pymupdf
.\.venv\Scripts\python.exe tools\capture_screens.py     # screenshots of every card block and panel, using the installed Edge
.\.venv\Scripts\python.exe tools\build_guide.py         # docs\Dashboard User Guide.docx
.\.venv\Scripts\python.exe tools\build_deck.py          # docs\Leadership Briefing.pptx
.\.venv\Scripts\python.exe tools\build_video.py         # docs\Platform Explainer.mp4 (first: pip install imageio-ffmpeg)
```

The wording for every card and graph lives in `tools/guide_content.py`. See [tools/README.md](tools/README.md) for the
Word step that refreshes the guide's table of contents, and for how the deck is checked.

## Public demo on Azure

`deploy/azure/` publishes the prototype as a public website on the **Free (F1) tier of Azure App Service**, which costs nothing.
It hosts this prototype; it does not build the Fabric and Power BI architecture, which has no free tier. See
[deploy/azure/README.md](deploy/azure/README.md) for what is created, the limits of the Free tier and what a public visitor can do.

```powershell
az login
.\deploy\azure\deploy.ps1 -AppName <globally-unique-name>
```

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
hip/pipelines/  bronze, silver, gold, quality, energy, orchestrator
hip/ai/     forecasting, predictive models, recommendations and alerts, energy and device models
hip/api/    FastAPI app, dashboards, Control Tower, security, workflow
web/        single-page client (no build step)
tests/      acceptance tests
tools/      regenerates the user guide, leadership deck and explainer video from the running app
deploy/     free-tier Azure deployment for a public demo
powerbi/    offline Power BI report (project files and Parquet data)
docs/       SRS, traceability matrix, Dashboard User Guide, Leadership Briefing
data/       generated: landing files, lakehouse, workflow database, model artefacts
```
