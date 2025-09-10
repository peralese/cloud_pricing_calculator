# Cloud Pricing Calculator

A Python tool that **recommends instance/VM sizes** and **prices workloads** — one cloud per run.

- ✅ **AWS**: Recommend + live pricing (AWS Pricing API & RDS add‑on)
- ✅ **Azure**: Recommend (CLI/SDK/cache) + pricing (Retail Prices API with local cache + overrides)
- ✅ **Validator** with **region validation** (AWS & Azure), report‑only

All outputs are timestamped and written to `./output/`.

---

## What’s New (September 2025)

**Stability & Quality**
- **NaN/blank‑safe parsing** across the pricing flow (no more `.strip()` on NaN from Excel).
- **True Excel output** when `--output` ends in `.xlsx`/`.xls` (uses pandas; CSV remains default).
- **Clear Azure price cache semantics** (`prices/azure_compute_cache_<region>.json`) and `--refresh-azure-prices`.
- **Safer field normalization** for `cloud`, `region`, `os`, `license_model`, `ebs_type`, etc.
- Clean‑up of duplicated helper logic in the recommender.

**Docs**
- CLI flags documented end‑to‑end; new Troubleshooting section.
- Explicit input schema for **recommend** vs **price** gates.

---

## Features

- **Single‑cloud runs**: `--cloud {aws|azure}` is required for both steps.
- **Sizing**
  - **AWS EC2**: current‑gen x86 families; profile‑aware (`balanced`, `compute`, `memory`). Diagnostics: `overprov_vcpu`, `overprov_mem_gib`, `fit_reason`.
  - **Azure VM**: sizes via **Azure CLI** (`az vm list-sizes`) or **Azure SDK**, with local **cache** fallback; canonical **region** handling.
- **Pricing**
  - **AWS**: On‑Demand hourly via Pricing API + monthly breakdown; optional **RDS** (Multi‑AZ supported).
  - **Azure**: Retail Prices API with **local cache** and optional **per‑SKU overrides** (JSON). Hourly + monthly totals.
- **Validator (report‑only)**
  - **Tier‑A (recommend gate):** requires `cloud`, `region`, and at least one of `vcpu` or `memory_gib` (>0).
  - **Tier‑B (pricing gate):** if `os`, `purchase_option`, `root_gb`, `root_type` missing → row becomes `rec_only` and is skipped during pricing.
  - Region sanity & “did‑you‑mean” suggestions (AWS `us-east-1`; Azure `eastus` format).
- **Outputs**
  - `recommend_<ts>.csv` — recommendations & diagnostics
  - `price_<ts>.csv` or `.xlsx` — hourly + monthly totals
  - `validator_report_<ts>.csv` — row status and fix hints

---

## Installation

```bash
# Activate your virtual environment first
pip install -U click pandas boto3 requests openpyxl xlsxwriter

# (Optional) Azure SDK route for sizing
pip install -U azure-identity azure-mgmt-compute

# Or use Azure CLI (recommended)
# Sign in and select subscription:
az login
az account set --subscription "<subscription id or name>"
```

> **Windows tip (PowerShell):** `.\.venv\Scripts\Activate.ps1`

---

## CLI Overview

```bash
python main.py recommend --cloud {aws|azure} --in <path.csv|.xlsx> [--region <slug>] [--strict]
python main.py price      --cloud {aws|azure} (--latest | --in <recommend.csv>) \
                          [--region <slug>] [--output <file.csv|.xlsx>] \
                          [--hours-per-month <int>] [--refresh-azure-prices] [--no-monthly]
```

**Common flags**
- `--cloud {aws|azure}`: required; runs are per‑cloud.
- `--in PATH`: input file for the command. `--latest` (price only) finds most recent recommend output.
- `--region`: preferred as a **per‑row** column; if supplied here, applies as default.
- `--output`: write results to a specific path. `.xlsx` triggers true Excel output.
- `--hours-per-month`: default 730.
- `--refresh-azure-prices`: force refresh of Azure retail cache for the target region.
- `--no-monthly`: compute hourly only.

**Helper commands (if enabled in your build)**
```bash
python main.py list-aws-regions
python main.py list-azure-regions
```

---

## Quick Start

### 1) Recommend

**AWS**
```bash
python main.py recommend --cloud aws --region us-east-1 --in servers.csv
```

**Azure**
```bash
# If using CLI for sizing:
az login
az account set --subscription "<sub>"

python main.py recommend --cloud azure --in servers.csv
# Tip: For per-row region, use canonical slugs (e.g., eastus, eastus2).
```

You’ll see a summary like:
```
Validation: rows=120 | ok=92 | rec_only=21 | error=7
Wrote recommendations -> output/recommend_YYYYMMDD-HHMMSS.csv
Wrote validator report -> output/validator_report_YYYYMMDD-HHMMSS.csv
```

### 2) Price

**Use the newest recommend file**
```bash
python main.py price --cloud azure --latest --output output/price_latest.xlsx
```

**Or point at a specific file**
```bash
python main.py price --cloud aws --in output/recommend_YYYYMMDD-HHMMSS.csv
```

---

## Validator Details

**Tier‑A (recommendation gate)** — blocks recommendation if missing/invalid:
- `cloud ∈ {aws, azure}`
- `region` (canonical; Azure aliases normalized with warnings; invalid → error)
- `vcpu` **or** `memory_gib` present and **> 0** (prefer both)

**Tier‑B (pricing gate)** — allows recommendation but **skips pricing** if missing:
- `os ∈ {linux, windows}` (case‑insensitive)
- `purchase_option ∈ {ondemand, spot, reserved}`
- `root_gb`, `root_type`

**Statuses per row**
- `ok` — recommend + price
- `rec_only` — recommend only; `pricing_note` explains missing fields
- `error` — dropped; see `validator_report` for fix hints

**Strict mode**
```bash
python main.py recommend --cloud azure --in servers.csv --strict
# Non-zero exit if any row is rec_only or error
```

**Report file**
- `validator_report_<ts>.csv` columns: `row_index,input_file,status,blocking_for,reasons,fix_hints`

---

## Input Schema (CSV/Excel)

**Minimum for recommendation**
```
cloud,region,vcpu,memory_gib
```

**Add these for pricing**
```
os,purchase_option,root_gb,root_type
```
**Optional price-impacting**
```
license_model (AWS: AWS|BYOL), ebs_gb, ebs_type, s3_gb, network_profile,
db_engine, db_instance_class, multi_az
```

**Region rules**
- **AWS**: `us-east-1`, `us-east-2`, `us-west-2`, …  
- **Azure**: `eastus`, `eastus2`, `westus2`, … (lowercase, no spaces).  
  Common aliases (e.g., `US-East`) are normalized; truly invalid slugs error with suggestions.

---

## Pricing Outputs

**CSV / Excel columns (representative)**:
```
provider,cloud,region,instance_type,os,license_model,
price_per_hour_usd,
monthly_compute_usd,monthly_ebs_usd,monthly_s3_usd,
monthly_network_usd,monthly_db_usd,monthly_total_usd,
pricing_note
```

- **Excel** outputs go to a single **Results** sheet.
- Hourly and monthly are both included unless `--no-monthly` is passed.

---

## Caching

**Azure compute retail prices**
- Stored at: `prices/azure_compute_cache_<region>.json`.
- Use `--refresh-azure-prices` to bypass cache and refresh from the API.
- If you see older misspellings (e.g., `cashe`), they’re safe to delete.

**EC2/VM catalogs**
- Local caches may be kept to accelerate recommendations. Deleting them forces a refresh via CLI/SDK/API depending on cloud.

---

## Troubleshooting

- **“.strip() on NaN” type errors**: fixed in this release via robust normalization; ensure you’re on the latest code.
- **Excel read issues**: `pip install openpyxl`; confirm the sheet/data range is valid; export to CSV if needed.
- **Azure region invalid**: use canonical slugs like `eastus`, `eastus2`. Try `python main.py list-azure-regions`.
- **AWS region missing**: pass `--region us-east-1` or include it per row.
- **No monthly totals**: you may have used `--no-monthly`. Remove that flag to restore monthly outputs.
- **RDS pricing empty**: ensure `db_engine`, `db_instance_class`, `multi_az` and `region` are set for that row.

---

## Roadmap (Next)

- [x] Stabilize pricing flow (NaN‑safe) and Excel writer
- [x] Clarify Azure cache + `--refresh-azure-prices`
- [ ] Unified cache layer with TTL and `--no-cache` override
- [ ] Multi‑cloud comparison output (side‑by‑side)
- [ ] Excel report with formatted Summary + per‑component tabs
- [ ] CI workflow (pytest on 3.10/3.11; ruff + black)
- [ ] “Architecture template” pricing (e.g., **web‑3AZ + RDS Multi‑AZ** end‑to‑end)

---

## License

MIT

---

## Author

**Erick Perales** — IT Architect | Cloud Migration Specialist
https://github.com/peralese


