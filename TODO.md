# FreshRoute AI - build tracker

Type `continue` to resume from the first unchecked item.

## Phase 1-2: Workspace and design
- [x] Inspect workspace (empty) and environment
- [x] Architecture, SQLite schema, forecasting design, page structure

## Phase 3: Backend
- [x] Config, database schema, repository layer
- [x] CSV and record validation
- [x] Waste calculations and unit handling
- [x] Forecasting engine (baselines, Random Forest, chronological evaluation, fallback)
- [x] Preparation recommendations with operational constraints
- [x] Analytics and rule-based insights
- [x] Reports (HTML, summary CSV, records CSV, PDF)
- [x] Synthetic demo data, load/reset
- [x] Flask pages, JSON API, CSRF, security headers, error pages

## Phase 4-5: Frontend
- [x] Design system, responsive shell, mobile drawer
- [x] Charts (built-in SVG renderer + Chart.js adapter)
- [x] Landing, dashboard, import, forecast, plan, records, analytics, reports, settings

## Phase 6: Demo mode
- [x] Synthetic dataset, auto-load on first run, reset without touching user data

## Phase 7: Quality
- [x] 159 unit/integration tests written and passing (unittest)
- [x] Browser end-to-end walk-through (Playwright + Chromium) passing, no console errors
- [x] Link/static/template audit (route test), mobile overflow check, demo-label checks
- [x] README, requirements, .gitignore, Windows helper scripts, VS Code config

## Phase 7b: Time Machine (what-if replay)
- [x] Policy refactored into shared `suggest_quantity`
- [x] `backtest.py`: leak-free replay, bounds for ran-out services, buffer sweep
- [x] `/time-machine` page, `/api/backtest`, two charts, dashboard link, 12 tests, browser check

## Phase 8: Delivery
- [x] Screenshots generated from the running app
- [x] ZIP archive built and verified by running the tests from an extracted copy

## Not done / not verified
- [ ] Run on a real Windows 11 machine (built and tested on Linux)
- [ ] Real Chart.js rendering (library could not be downloaded in the build sandbox; run `python scripts\download_chartjs.py`; only a stub of the adapter was tested)
- [ ] Run the suite under pytest (not installable offline; tests are plain unittest)
- [ ] Login / multi-user support (intentionally omitted; single-user local tool)
- [ ] Unit conversion between kg and servings
- [ ] Scheduled/automatic daily plans
