# Cloud Pricing Calculator

A Python tool that **recommends instance/VM sizes** and **prices workloads** — one cloud per run.

- ✅ **AWS**: Recommend + live pricing (Pricing API) + optional **RDS** instance-hours (Multi‑AZ supported)
- ✅ **Azure**: Recommend (CLI/SDK/cache) + pricing (Retail Prices API with local cache + overrides)
- ✅ **Validator** (report‑only) with **region validation** for AWS & Azure

All outputs now land in **date/time‑stamped run folders** under `./output/`.

---

## What’s New (September 2025)

**CLI & UX**
- Click‑only CLI (`recommend`, `price`) — no interactive prompts.
- `--latest` now **searches recursively** and finds nested outputs.
- `recommend` and `validator_report` write to the **same run folder**; `price` writes `price.csv` to that folder too:
  ```
  output/
    2025-09-07/
      221441/
        recommend.csv
        validator_report.csv
        price.csv
  ```

**Validation**
- Tier‑A (**recommend gate**): `cloud`, `region`, and at least one of `vcpu` or `memory_gib` (>0).
- Tier‑B (**pricing gate**): `os`, `purchase_option`, `root_gb`, `root_type`; missing → row becomes `rec_only` (recommend only).
- Robust `NaN/null/""` handling; numeric sanity; **region checks** with “did‑you‑mean” hints.
- Azure **region normalization** (e.g., `US‑East` → `eastus`) during recommendation.

**Azure pricing cache**
- Clear cache location: `prices/azure_compute_cache_<region>.json`.
- `--refresh-azure-prices` forces a live refresh before pricing.

**Housekeeping**
- Removed argparse/duplicate code; unified helpers; safer normalization of common fields.

---

## Features

- **Single‑cloud runs**: `--cloud {aws|azure}` is required for both commands.
- **Sizing**
  - **AWS**: current‑gen x86 families; profile‑aware (`balanced`, `compute`, `memory`). Diagnostics: `overprov_vcpu`, `overprov_mem_gib`, `fit_reason`.
  - **Azure**: sizes via **Azure CLI** (`az vm list-sizes`) or **Azure SDK** with local **cache** fallback; canonical region handling.
- **Pricing**
  - **AWS**: On‑Demand hourly via Pricing API + monthly breakdown; **RDS** instance-hours (Multi‑AZ uplift) when DB fields are provided.
  - **Azure**: Retail Prices API with **local cache** and optional **per‑SKU overrides**. Writes hourly and monthly totals.
- **Validator (report‑only)**
  - Tier‑A blocks recommendation; Tier‑B allows recommendation but skips pricing.
  - Region sanity with suggestions (AWS: `us-east-1`; Azure: `eastus`, `eastus2`, ...).

---

## Installation

```bash
# In your virtual environment
pip install -U click pandas boto3 requests openpyxl xlsxwriter

# (Optional) Azure sizing via SDK
pip install -U azure-identity azure-mgmt-compute

# Or use Azure CLI (recommended for sizing)
az login
az account set --subscription "<subscription id or name>"
```

> **Windows tip (PowerShell):** `.\.venv\Scripts\Activate.ps1`

---

## CLI Overview

```bash
python main.py recommend --cloud {aws|azure} --in <path.csv|.xlsx> [--region <slug>] [--strict] [--output <file>]
python main.py price      --cloud {aws|azure} (--latest | --in <recommend.csv|.xlsx>)                           [--region <slug>] [--output <file.csv|.xlsx>]                           [--hours-per-month <int>] [--refresh-azure-prices] [--no-monthly]
```

**Flags at a glance**
- `--cloud {aws|azure}`: required; one cloud per run.
- `--in PATH`: input file. For `price`, `--latest` finds the newest recommend output **recursively**.
- `--region`: preferred as a per‑row column; if given here, used as default (e.g., `us-east-1`, `eastus`).
- `--output`: explicit output path (CSV by default; `.xlsx`/`.xls` writes Excel).
- `--hours-per-month`: monthly hours (default **730**).
- `--refresh-azure-prices`: bypass cache for Azure pricing.
- `--no-monthly`: write hourly price only.

**Helper commands**
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
# If using CLI:
az login
az account set --subscription "<sub>"

python main.py recommend --cloud azure --in servers.csv
# Per-row regions should be canonical (e.g., eastus, eastus2).
```

Expect console summary like:
```
Validation: rows=22 | ok=18 | rec_only=3 | error=1
Wrote recommendations -> output/YYYY-MM-DD/HHMMSS/recommend.csv
Wrote validator report -> output/YYYY-MM-DD/HHMMSS/validator_report.csv
```

### 2) Price

**Newest recommend automatically**
```bash
python main.py price --cloud azure --latest
# → writes output/YYYY-MM-DD/HHMMSS/price.csv
```

**Specific recommend file**
```bash
python main.py price --cloud aws --in output/YYYY-MM-DD/HHMMSS/recommend.csv --region us-east-1
```

---

## Validator Details

**Tier‑A (recommendation gate)** — blocks recommendation if missing/invalid:
- `cloud ∈ {aws, azure}`
- `region` canonical (Azure aliases normalized with warnings; invalid → error)
- `vcpu` **or** `memory_gib` present and **> 0** (prefer both)

**Tier‑B (pricing gate)** — allows recommendation but **skips pricing** if missing:
- `os ∈ {linux, windows}` (case‑insensitive)
- `purchase_option ∈ {ondemand, spot, reserved}`
- `root_gb`, `root_type`

**Row statuses**
- `ok` — recommend + price
- `rec_only` — recommend only; `pricing_note` explains missing fields
- `error` — dropped; see `validator_report` for fix hints

**Strict mode**
```bash
python main.py recommend --cloud azure --in servers.csv --strict
# Non-zero exit if any row is rec_only or error
```

**Report file**
- `validator_report.csv` columns: `row_index,input_file,status,blocking_for,reasons,fix_hints` (in the same run folder)

---

## Input Schema (CSV/Excel)

**Minimum for recommendation**
```
cloud,region,vcpu,memory_gib
```

**Add for pricing**
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
  Aliases like `US‑East` are normalized; truly invalid regions error with suggestions.

---

## Pricing Outputs

Representative columns:
```
provider,cloud,region,instance_type,os,license_model,
price_per_hour_usd,
monthly_compute_usd,monthly_ebs_usd,monthly_s3_usd,
monthly_network_usd,monthly_db_usd,monthly_total_usd,
pricing_note
```

- Hourly and monthly are emitted unless `--no-monthly` is used.
- If **RDS** columns are supplied (engine/class/region/license/multi_az), `monthly_db_usd` is populated.

---

## Caching & Data Sources

**Azure compute prices**
- Cache file per region: `prices/azure_compute_cache_<region>.json`.
- Use `--refresh-azure-prices` to fetch fresh prices and update the cache.

**AWS pricing**
- On‑Demand EC2 via Pricing API; RDS instance-hours via service pricing lookups.

**Catalogs**
- Sizing catalogs (EC2/VM sizes) may be cached locally. Deleting caches forces a refresh via CLI/SDK/API.

---

## Troubleshooting

- **Azure region invalid** → Use canonical slugs (`eastus`, `eastus2`). Try `python main.py list-azure-regions`.
- **AWS region required** → Pass `--region us-east-1` for AWS runs or include per‑row.
- **Row became `rec_only`** → Add Tier‑B fields (`os`, `purchase_option`, `root_gb`, `root_type`).
- **Excel read** → `pip install openpyxl`; ensure the data sheet exists; or export to CSV.
- **Azure pricing stale** → `--refresh-azure-prices` to force live refresh.

---

## Roadmap (Next Up)

- Azure price cache **TTL** (auto‑refresh stale caches, e.g., older than 7 days)
- Azure preflight checks module (CLI/login/subscription/provider) enabled by default
- Per‑run **summary.csv** (totals/averages) in the run folder
- Excel report polish (styled Results + Summary sheets)
- Multi‑cloud comparison output (side‑by‑side)
- CI workflow (pytest + ruff + black); golden price tests with tolerances

---

## License

MIT

---

## Author

**Erick Perales** — IT Architect | Cloud Migration Specialist
https://github.com/peralese


