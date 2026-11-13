# Requirements traceability

How each requirement in the SRS is met in the local prototype. Status: **Done** (implemented and
exercised), **Partial** (implemented in reduced form; the gap is stated), **Not in prototype**.

## 3. Azure – data collection layer

| Requirement | Status | Where |
|---|---|---|
| FR-AZ-001 Multiple PMS platforms | Done | Two vendors with different schemas, codes and date formats, unified in `silver.reservation` |
| FR-AZ-002 POS, ERP, CRM, ticketing, loyalty, booking, procurement | Done | 29 feeds in `config/sources.json` |
| FR-AZ-003 REST, SOAP, database, SFTP, file, streaming | Partial | All feeds arrive as files in the landing zone; the intended connector per feed is recorded as metadata only |
| FR-AZ-004 Configurable pipeline per source | Done | One registry entry per feed drives `bronze.py` |
| FR-AZ-005 Incremental ingestion by watermark | Done | `ops.watermarks`; test `test_incremental_ingestion_is_idempotent` |
| FR-AZ-006 Source metadata, timestamps, status, counts | Done | `ops.ingestion_log`, bronze `_source_*`, `_batch_id`, `_ingested_at`, `_row_hash` |
| FR-AZ-007 Retry and quarantine | Done | Three attempts with back-off; `ops.quarantine` |
| FR-AZ-008 Centrally managed credentials | Not in prototype | No credentials are needed locally; Key Vault in the target |
| FR-AZ-009 Property-specific configuration without duplication | Done | Properties are rows in `config/properties.json`; feeds pick up per-property files by pattern |
| FR-AZ-010 Configurable frequency | Partial | Target frequency stored per feed and shown in the ingestion monitor; runs are manual batches |

## 4. Fabric – medallion architecture

| Requirement | Status | Where |
|---|---|---|
| 4.1 Bronze: raw, source / property / vertical identification, timestamps, schema evolution, replay, audit | Done | `bronze.*` (all columns kept as text), new source columns added automatically, full reload by regenerating |
| 4.2 Silver: standard formats, identifiers, de-duplication, validation, harmonisation, relationships, SCD, lineage | Done | `silver.py`; SCD2 in `silver.customer_history`; lineage in `ops.lineage` |
| 4.2 Master data entities (14) | Done | Property, room type, outlet (hotel, restaurant, store, spa, park), attraction, product, spa service, guest, employee, supplier, campaign, currency, calendar |
| 4.2 Unified guest identity with consent | Done | `silver.guest_identity`, `gold.dim_guest`; consent carried from CRM and applied in recommendations |
| 4.3 Gold data products (9 marts) | Done | Facts and dimensions per domain plus `mart_daily_revenue`, `mart_executive_daily`, `mart_guest_360` |

## 5. Business domains

Every FR below is a panel or KPI on the named dashboard, computed from Gold.

| Requirements | Status | Where |
|---|---|---|
| FR-HOT-001 to 008 | Done | Hotels dashboard: occupancy, ADR, RevPAR, pickup, channels, segments, cancellations, room categories, forecast |
| FR-RES-001 to 008 | Done | Restaurants dashboard: covers, seat utilisation, spend, menu engineering, time slots, table no-shows |
| FR-WP-001 to 007 | Done | Waterpark dashboard: attendance, capacity, attraction utilisation, queues, hourly traffic, ticket mix |
| FR-RET-001 to 007 | Done | Retail dashboard: sales, margin, inventory exceptions, stock-outs, shopper behaviour |
| FR-SPA-001 to 006 | Done | Spa dashboard: appointments, utilisation, therapists, cancellations, repeat rate |
| FR-MKT-001 to 007 | Done | Marketing dashboard: spend, CAC, ROI, loyalty, lifetime value, cross-vertical engagement |
| FR-FIN-001 to 009 | Done | Finance dashboard: consolidated P&L, budget variance, cost centres, procurement, anomalies, forecasts |
| AI use cases per domain | Partial | Implemented: occupancy, demand and revenue forecasts; cancellation; churn; segmentation and lifetime value; covers and spend; attendance and queues; retail demand, stock-out and replenishment; spa demand; conversion; OPEX and profitability; expense, procurement and discount anomalies. Not implemented as separate models: staffing optimisation, product affinity, assortment optimisation, next-best-offer, food wastage (these appear only as rule-based actions inside recommendations) |

## 6. Dashboards

| Requirement | Status | Where |
|---|---|---|
| 6.1 to 6.8 Executive and seven domain dashboards with listed KPIs | Done | `hip/api/dashboards.py` |
| 6.9 Consistent design language, standard KPI definitions | Done | One renderer for all pages; definitions from `ops.kpi_glossary` on each tile |
| 6.9 Global property, vertical, date and period filters | Done | Top bar; vertical is selected by page |
| 6.9 Drill-through enterprise to property and department | Partial | Property rows drill to the property view; department level is reached through the domain pages |
| 6.9 Role-based data access | Done | `hip/api/security.py`; tests `test_row_level_security_*`, `test_vertical_access_*` |
| 6.9 Mobile-responsive executive dashboards | Done | Single-column layout and collapsible navigation below 820px |
| 6.9 Export subject to permissions | Done | Per-panel CSV, permission-checked and audited |
| 6.9 Actual, budget and forecast clearly distinct | Done | Solid, grey dashed and orange dashed with interval, with a legend on every page |
| 6.9 AI insights linked to measures | Done | Insights panel names its measure; recommended actions link to the Control Tower |
| Power BI as the delivery tool | Not in prototype | Replaced locally by the web client; Gold is modelled as a star schema ready for a semantic model |

## 7. AI Control Tower

| Requirement | Status | Where |
|---|---|---|
| 7.1 Eleven logical components | Done | Gold intake, features, training and validation, registry and versions, batch inference, forecasts, rules engine, prioritisation, alerts, approval and action tracking, monitoring. Near-real-time inference is not implemented |
| 7.2 Fourteen predictive models | Done | See `ai.model_registry` (15 deployed models) |
| 7.3 Twelve prescriptive scenarios | Done | `hip/ai/recommend.py`, plus supplier, budget-allocation and discount-leakage scenarios |
| 7.4 A Enterprise Intelligence Overview | Done | Health, forecast revenue and profit, exceptions, top recommendations, opportunities, risk indicators |
| 7.4 B Forecasting Workspace | Done | Property and vertical selection, 30/60/90 days, actual versus forecast, intervals, accuracy history, scenarios |
| 7.4 C Recommendation fields | Done | All twelve listed fields; test `test_every_recommendation_carries_the_required_fields` |
| 7.4 D Alert management | Done | Three severities, acknowledge, assign, escalate, resolve with comments |
| 7.4 E Model monitoring | Done | Name, version, accuracy, error, drift, data quality, training and inference dates, deployment status |
| 7.5 Governance | Partial | Objectives, versions, training range, monitoring, confidence, explainability, human approval, consent filtering are in place. Automated execution through approved interfaces is out of scope (SRS 2.2). Drift raises an alert; retraining is a manual pipeline run |

## 8 to 11. Data model, scale, security, non-functional

| Requirement | Status | Where |
|---|---|---|
| 8.1 Thirteen governed KPIs | Done | `config/kpi_definitions.json`; test `test_occupancy_adr_revpar_follow_enterprise_definitions` |
| 8.2 Conformed dimensions and facts at correct grain | Done | All 14 dimensions and 17 facts; test `test_facts_keep_their_grain` |
| 9 Multi-property: logical separation, benchmarking, brand, region, property, department reporting, currency conversion | Done | Property, brand and region on `dim_property`; USD conversion in Silver |
| 9 Property time zones and operating calendars | Partial | Time zone and event calendar stored per property; timestamps are kept in local property time and not converted |
| 9 Configurable retention | Partial | Retention recorded per layer in the catalogue; not enforced |
| 10.1 Entra ID, RBAC, least privilege, RLS, audit logging, classification | Partial | Simulated personas; RBAC, row-level filtering, audit log and classification are real. Encryption, managed identities and network controls are platform concerns for the Azure build |
| 10.2 Catalogue, glossary, lineage, ownership, stewardship, quality rules, classification, retention, metadata | Done | Data & Governance page. Access-approval workflow is not implemented |
| 10.3 Privacy | Partial | Consent, minimisation, pseudonymisation, masking, no personal data in model features. Subject access, deletion and cross-border controls are not implemented |
| 11 Performance under 5 seconds | Done | Every dashboard responds in under 0.5 seconds on the synthetic volume |
| 11 Reliability, auditability, maintainability, observability | Done | Retry, reconciliation, run logs, config-driven modules, monitoring pages |
| 11 Availability, disaster recovery, freshness of 5 to 15 minutes | Not in prototype | Platform concerns for the Azure build |

## 12 to 16. Method, environments, acceptance

| Requirement | Status | Where |
|---|---|---|
| 12 Specification-driven development | Partial | This matrix links requirements to code and tests. Per-feature specifications in the 15-part structure of 12.2 have not been written |
| 12.3 HOTEL-FORECAST-001 acceptance criteria | Done | Property and room-category level, 30/60/90 days, actual with forecast, intervals, accuracy metrics, recommendations traceable to model output; tests `test_forecasts_*`, `test_hotel_forecast_*` |
| 13 Dev / SIT / UAT / production, CI/CD | Not in prototype | Single local environment |
| 16 Acceptance criteria 1 to 9 | Done locally | Covered by the 25 tests in `tests/test_platform.py` on synthetic sources |
| 16 Acceptance criteria 10 to 12 | Not in prototype | Operational support, knowledge transfer and stakeholder sign-off |
