# Cloud Pricing Calculator

A Python tool that **recommends instance/VM sizes** and **prices workloads** — one cloud per run.

- ✅ **AWS**: Recommend + live pricing (Pricing API) + optional **RDS** instance-hours (Multi‑AZ supported)
- ✅ **Azure**: Recommend (CLI/SDK/cache) + pricing (Retail Prices API with local cache + overrides)
- ✅ **Validator** (report‑only) with **region validation** for AWS & Azure
- ✅ **Per‑run summaries**: `summary.csv` + `summary.json` + Excel **Summary** sheet
- ✅ **Excel polish**: autosize columns, freeze header, and auto‑filter on the Results sheet

All outputs now land in **date/time‑stamped run folders** under `./output/`.

---

## What’s New (September 2025)

- Click‑only CLI (`recommend`, `price`) — no interactive prompts.
- `--latest` now searches recursively and finds nested outputs.
- `recommend` and `validator_report` write to the same run folder; `price` writes `price.csv` there too.
- **New:** Per‑run summaries are generated automatically:
  - Files: `summary.csv` (metric,value), `summary.json` (machine‑readable), and `summary_top5.csv` (top 5 by monthly total when pricing exists).
  - Excel outputs also include a **Summary** worksheet (and a “Top 5 (Monthly)” sheet for priced runs).
- **New:** Excel output polish (autosized columns, frozen header row, auto‑filter).
- Validator: Tier‑A (cloud/region + vCPU or memory > 0), Tier‑B (os/purchase_option/root_gb/root_type).
- Robust NaN/null handling; numeric sanity; region checks with “did‑you‑mean” hints.
- Azure region normalization (aliases → canonical).
- Azure pricing cache: `prices/azure_compute_cache_<region>.json`
- **New:** `--azure-cache-ttl DAYS` controls the Azure retail price cache freshness (default **7** days). `--refresh-azure-prices` still forces a refresh.

---

## Installation

```bash
python -m venv .venv && . .venv/Scripts/activate  # (Windows) or source .venv/bin/activate (macOS/Linux)
pip install -U click pandas openpyxl xlsxwriter boto3 requests
pip install -U azure-identity azure-mgmt-compute   # optional for Azure sizing via SDK
az login && az account set --subscription "<sub>"  # or use Azure CLI
```

> **Note:** For Azure recommendations without the SDK, Azure CLI is used automatically if available. Ensure `az vm list-sizes` works for your target region(s).

---

## CLI Overview

```bash
python main.py recommend --cloud {aws|azure} --in <file.csv|.xlsx> [--region <slug>] [--strict] \
                         [--output <file>] [--validator-report <file>]

python main.py price      --cloud {aws|azure} (--latest | --in <recommend.csv|.xlsx>) \
                          [--region <slug>] [--output <file>] [--hours-per-month N] \
                          [--refresh-azure-prices] [--azure-cache-ttl DAYS] [--no-monthly]

# Helpers
python main.py list-aws-regions
python main.py list-azure-regions
```

### Key Flags

- `--latest`: Finds the most recent `recommend.*` under `./output/**`.
- `--output`: If set to `.xlsx`, Excel will include **Results** (styled) and **Summary** sheets.
- `--azure-cache-ttl DAYS`: Keep Azure retail price cache for `DAYS` before refreshing (default **7**).
- `--refresh-azure-prices`: Force bypass the cache (fetch fresh retail pricing).
- `--strict` (recommend): Non‑zero exit if any row is `rec_only` or `error`.

---

## Quick Start

**AWS**
```bash
python main.py recommend --cloud aws --region us-east-1 --in servers.csv --output output/recommend.xlsx
python main.py price --cloud aws --latest --hours-per-month 730 --output output/price.xlsx
```

**Azure**
```bash
az login
az account set --subscription "<sub>"
python main.py recommend --cloud azure --in servers.csv --output output/recommend.xlsx
python main.py price --cloud azure --latest --azure-cache-ttl 7 --output output/price.xlsx
```

**Outputs per run:**
```
output/YYYY-MM-DD/HHMMSS/
  recommend.csv|xlsx
  validator_report.csv
  price.csv|xlsx                     # when you run `price`
  summary.csv                        # always generated
  summary.json                       # machine-readable metrics
  summary_top5.csv                   # top 5 by monthly total (only when price exists)
```

- Excel `.xlsx` files include:
  - **Results**: autosized columns, frozen header, auto‑filter
  - **Summary**: run totals/averages and validator counts
  - **Top 5 (Monthly)**: highest monthly totals (when pricing runs)

---

## Validator

- **Tier‑A (recommendation gate)** — `cloud`, `region`, and one of `vcpu`/`memory_gib` (>0).
- **Tier‑B (pricing gate)** — `os`, `purchase_option`, `root_gb`, `root_type`.
- Row statuses: `ok`, `rec_only`, `error`.
- `--strict` → non‑zero exit if any row rec_only/error.

---

## Input Schema

Minimum:
```
cloud,region,vcpu,memory_gib
```

For pricing:
```
os,purchase_option,root_gb,root_type
```

Optional:
```
license_model, ebs_gb, ebs_type, s3_gb, network_profile,
db_engine, db_instance_class, multi_az
```

Notes:
- `license_model`: `AWS` vs `BYOL` (BYOL → compute priced as Linux).
- `network_profile`: one of `low|medium|high` (mapped to GB egress/month via env).
- For Azure, per‑row `region` is used; for AWS you can pass `--region` to apply to all rows.

---

## Pricing Model (Monthly)

We compute:

- `monthly_compute_usd` = `price_per_hour_usd × hours_per_month` (default `730`)
- `monthly_ebs_usd`:
  - `gp3`: `$0.08/GB-month` (override via `EBS_GP3_GB_MONTH`)
  - `io1`: `$0.125/GB-month` (override via `EBS_IO1_GB_MONTH`)
- `monthly_s3_usd` = `S3_STD_GB_MONTH × s3_gb` (default `$0.023/GB-month`)
- `monthly_network_usd` = `DTO_GB_PRICE × (egress GB from profile)` (defaults: low=50, med=500, high=5000; `DTO_GB_PRICE` default `$0.09/GB`)
- `monthly_db_usd` (AWS only): RDS On‑Demand hours for specified `db_engine` + `db_instance_class` (+Multi‑AZ if set)
- `monthly_total_usd` = sum of the above

All constants can be overridden via environment variables.

---

## Azure Retail Price Cache

- Cache file per region: `prices/azure_compute_cache_<region>.json`
- Cache entries are keyed by SKU core + OS (e.g., `D4s v5|linux`).
- Use `--azure-cache-ttl DAYS` (default **7**) to control staleness; if the cache file is older than TTL, a refresh is performed.
- `--refresh-azure-prices` bypasses the cache entirely (always fetch fresh).
- Optional user override file: `prices/azure_compute_prices.json`
  ```json
  [
    {"region": "eastus", "sku": "Standard_D4s_v5", "os": "linux", "license_model": "BYOL", "hourly": 0.20}
  ]
  ```

---

## Examples

**Recommend + Price (CSV inputs, XLSX outputs with Summary):**
```bash
python main.py recommend --cloud aws --region us-east-1 --in servers.csv --output output/run1/recommend.xlsx
python main.py price --cloud aws --in output/run1/recommend.xlsx --output output/run1/price.xlsx
```

**Azure with forced refresh:**
```bash
python main.py price --cloud azure --latest --refresh-azure-prices --output output/price.xlsx
```

**List Regions:**
```bash
python main.py list-aws-regions
python main.py list-azure-regions
```

---

## Troubleshooting

- **Azure preflight failed**: Ensure Azure CLI is installed and logged in.
  ```bash
  az login
  az account set --subscription "<sub>"
  ```
  For SDK path, set `AZURE_SUBSCRIPTION_ID` or rely on CLI auth via `DefaultAzureCredential`.

- **No EC2 price found**: Confirm region is supported and OS matches (`Linux`, `Windows`, `RHEL`, `SUSE`).

- **Excel write error**: Ensure `openpyxl` and `xlsxwriter` are installed for append/format operations.

---

## Roadmap

- Azure price cache TTL (done, CLI flag exposed)
- Azure preflight checks (expand beyond Microsoft.Compute)
- Per‑run `summary.csv` with totals/averages (done) + `summary.json` (done)
- Excel report polish (done: styled Results + Summary + Top 5)
- Multi‑cloud comparison output
- CI workflow (pytest + ruff + black; golden tests)

---

## License

MIT

---

## Author

**Erick Perales** — IT Architect | Cloud Migration Specialist  
https://github.com/peralese
