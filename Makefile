# RE-Maps — development tasks.
#
# Quick start on a fresh clone:
#     make setup           install backend + frontend dependencies
#     make db-create       create the database and enable PostGIS
#     make migrate         apply the schema
#     make data-download   fetch the open datasets (~3.5 GB)
#     make ingest          load everything (takes a while; resumable)
#     make dev             run the API and the frontend together

SHELL := /bin/bash
PY    := backend/.venv/bin/python
PIP   := backend/.venv/bin/pip
RUFF  := backend/.venv/bin/ruff
PYTEST:= backend/.venv/bin/pytest

# Homebrew installs PostgreSQL's binaries outside the default PATH on macOS.
PG_BIN := $(shell brew --prefix postgresql@18 2>/dev/null)/bin
export PATH := $(PG_BIN):$(PATH)

DB_NAME ?= remaps
RAW     := data/raw
PPD_URL := http://prod.publicdata.landregistry.gov.uk.s3-website-eu-west-1.amazonaws.com
HPI_URL := http://publicdata.landregistry.gov.uk/market-trend-data/house-price-index-data
# Years of Price Paid data to fetch. Override to load more or fewer:
#     make data-download PPD_YEARS="2015 2016 2017"
PPD_YEARS ?= 2021 2022 2023 2024 2025 2026
# UK HPI release to use. Check gov.uk for the latest month.
HPI_MONTH ?= 2026-06
DVF_YEARS ?= 2021 2022 2023 2024 2025
# French departments to load. Empty means every department (much larger).
FR_DEPTS  ?= 75 69 13 33 44 31 59 34

.DEFAULT_GOAL := help
.PHONY: help setup setup-backend setup-frontend db-create db-drop migrate \
        data-download data-download-ppd data-download-hpi data-download-geo \
        data-download-postcodes data-download-dvf ingest ingest-uk ingest-fr \
        ingest-reference stats evaluate api web dev test lint typecheck \
        build check clean

help:  ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-22s\033[0m %s\n", $$1, $$2}'

# --- setup -----------------------------------------------------------------

setup: setup-backend setup-frontend  ## Install all dependencies

setup-backend:  ## Create the Python venv and install backend dependencies
	@command -v python3.12 >/dev/null 2>&1 \
	  && python3.12 -m venv backend/.venv \
	  || python3 -m venv backend/.venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r backend/requirements.txt
	@echo "backend ready ($$($(PY) -V))"

setup-frontend:  ## Install frontend dependencies
	cd frontend && npm install --no-fund --no-audit

# --- database --------------------------------------------------------------

db-create:  ## Create the database and enable PostGIS
	createdb $(DB_NAME) 2>/dev/null || echo "database $(DB_NAME) already exists"
	psql -d $(DB_NAME) -c "CREATE EXTENSION IF NOT EXISTS postgis;"
	@psql -d $(DB_NAME) -tAc "SELECT 'PostGIS ' || postgis_lib_version();"

db-drop:  ## Drop the database (destroys all ingested data)
	@read -p "Drop database $(DB_NAME) and all ingested data? [y/N] " ok; \
	 [[ $$ok == [yY] ]] && dropdb --if-exists $(DB_NAME) && echo dropped || echo cancelled

migrate:  ## Apply schema migrations
	cd backend && .venv/bin/python migrate.py

# --- data download ---------------------------------------------------------

data-download: data-download-geo data-download-postcodes data-download-hpi \
               data-download-ppd data-download-dvf  ## Fetch every dataset

data-download-geo:  ## Country + sub-national boundaries (Natural Earth, ~54 MB)
	@mkdir -p $(RAW)/geo
	curl -sL --retry 3 -o $(RAW)/geo/ne_10m_admin_0_countries.geojson \
	  https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson
	curl -sL --retry 3 -o $(RAW)/geo/ne_10m_admin_1_states_provinces.geojson \
	  https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_1_states_provinces.geojson
	@echo "geo: $$(du -h $(RAW)/geo | tail -1)"

data-download-postcodes:  ## UK postcode centroids (Open Postcode Geo, ~66 MB)
	@mkdir -p $(RAW)/postcodes
	curl -sL --retry 3 -o $(RAW)/postcodes/open_postcode_geo.csv.zip \
	  https://www.getthedata.com/downloads/open_postcode_geo.csv.zip
	cd $(RAW)/postcodes && unzip -oq open_postcode_geo.csv.zip
	@echo "postcodes: $$(du -h $(RAW)/postcodes | tail -1)"

data-download-hpi:  ## UK House Price Index (~35 MB)
	@mkdir -p $(RAW)/hpi
	curl -sL --retry 3 -o $(RAW)/hpi/UK-HPI-full-file-$(HPI_MONTH).csv \
	  $(HPI_URL)/UK-HPI-full-file-$(HPI_MONTH).csv
	@echo "hpi: $$(du -h $(RAW)/hpi | tail -1)"

data-download-ppd:  ## HM Land Registry Price Paid Data, per year (~40 MB/yr gzipped)
	@mkdir -p $(RAW)/ppd
	@for y in $(PPD_YEARS); do \
	  if [ -s $(RAW)/ppd/pp-$$y.csv.gz ] || [ -s $(RAW)/ppd/pp-$$y.csv ]; then \
	    echo "  pp-$$y already present"; \
	  else \
	    echo "  downloading pp-$$y..."; \
	    curl -sL --retry 3 -o $(RAW)/ppd/pp-$$y.csv "$(PPD_URL)/pp-$$y.csv" \
	      && gzip -1 -f $(RAW)/ppd/pp-$$y.csv; \
	  fi; \
	done
	@echo "ppd: $$(du -h $(RAW)/ppd | tail -1)"

data-download-dvf:  ## France geo-DVF, per year (~100 MB/yr gzipped)
	@mkdir -p $(RAW)/dvf
	@for y in $(DVF_YEARS); do \
	  if [ -s $(RAW)/dvf/dvf-$$y.csv.gz ]; then echo "  dvf-$$y already present"; else \
	    echo "  downloading dvf-$$y..."; \
	    curl -sL --retry 3 -o $(RAW)/dvf/dvf-$$y.csv.gz \
	      "https://files.data.gouv.fr/geo-dvf/latest/csv/$$y/full.csv.gz"; \
	  fi; \
	done
	@echo "dvf: $$(du -h $(RAW)/dvf | tail -1)"

# --- ingestion -------------------------------------------------------------

ingest: ingest-reference ingest-uk ingest-fr ingest-world stats  ## Load everything
	@echo
	@echo "ingestion complete. Coverage:"
	@psql -d $(DB_NAME) -c "SELECT country_iso2, region_code, transaction_level_data, market_index, forecast_supported, historical_from, historical_to FROM provider_coverage ORDER BY 1,2 NULLS FIRST;"

ingest-reference:  ## Data-source registry, boundaries, postcode gazetteer
	cd backend && .venv/bin/python -m ingest.sources
	cd backend && .venv/bin/python -m ingest.countries
	cd backend && .venv/bin/python -m ingest.regions
	cd backend && .venv/bin/python -m ingest.postcodes

ingest-uk:  ## HM Land Registry Price Paid Data + UK House Price Index
	cd backend && .venv/bin/python -m ingest.uk_hpi
	cd backend && .venv/bin/python -m ingest.uk_ppd
	cd backend && .venv/bin/python -c "import logging; logging.basicConfig(level=logging.INFO, format='%(message)s'); from ingest.uk_hpi import link_districts; link_districts()"

ingest-world:  ## Official indices for 29 further jurisdictions (Eurostat, US FHFA)
	@echo "These sources publish an INDEX, not prices: growth only, no price level."
	cd backend && .venv/bin/python -m ingest.world_stats

ingest-fr:  ## France geo-DVF + derived local index
	cd backend && .venv/bin/python -m ingest.fr_dvf $(if $(FR_DEPTS),--departments $(FR_DEPTS),)
	cd backend && .venv/bin/python -m ingest.fr_index

stats:  ## Recompute map aggregates and the coverage registry
	cd backend && .venv/bin/python -m ingest.area_stats
	cd backend && .venv/bin/python -m ingest.coverage

# --- modelling -------------------------------------------------------------

evaluate:  ## Measure AVM accuracy and calibrate forecast intervals
	cd backend && .venv/bin/python -m ml.backtest_forecast
	cd backend && .venv/bin/python -m ml.eval_avm --limit 400

calibrate:  ## Sweep the forecast shrinkage parameters against real history
	cd backend && .venv/bin/python -m ml.backtest_forecast --calibrate

# --- running ---------------------------------------------------------------

api:  ## Run the API (http://127.0.0.1:8000)
	cd backend && .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

web:  ## Run the frontend (http://localhost:3000)
	cd frontend && npm run dev

dev:  ## Run API and frontend together
	@trap 'kill 0' EXIT INT TERM; \
	 ( cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 ) & \
	 ( cd frontend && npm run dev ) & \
	 wait

# --- quality ---------------------------------------------------------------

test:  ## Run the test suite
	cd backend && .venv/bin/python -m pytest

test-unit:  ## Run only tests that need no database
	cd backend && .venv/bin/python -m pytest -m "not db"

lint:  ## Lint the backend
	$(RUFF) check backend/app backend/ingest backend/ml backend/tests backend/migrate.py

typecheck:  ## Type-check the frontend
	cd frontend && npx tsc --noEmit

build:  ## Production build of the frontend
	cd frontend && npm run build

check: lint typecheck test  ## Everything CI would run

clean:  ## Remove build artefacts (keeps ingested data and downloads)
	rm -rf frontend/.next backend/.pytest_cache backend/.ruff_cache
	find backend -name __pycache__ -type d -prune -exec rm -rf {} +
