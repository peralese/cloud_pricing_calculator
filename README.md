# Cloud Pricing Calculator

A Python tool that **recommends instance/VM sizes** and **prices workloads** — one cloud per run.

- ✅ **AWS**: Recommend + live pricing (Pricing API) + optional **RDS** instance-hours (Multi‑AZ supported)
- ✅ **Azure**: Recommend (CLI/SDK/cache) + pricing (Retail Prices API with local cache + overrides)
- ✅ **Validator** (report‑only) with **region validation** for AWS & Azure

All outputs now land in **date/time‑stamped run folders** under `./output/`.

---

## What’s New (September 2025)

- Click‑only CLI (`recommend`, `price`) — no interactive prompts.
- `--latest` now searches recursively and finds nested outputs.
- `recommend` and `validator_report` write to the same run folder; `price` writes `price.csv` there too.
- Validator: Tier‑A (cloud/region + vCPU or memory > 0), Tier‑B (os/purchase_option/root_gb/root_type).
- Robust NaN/null handling; numeric sanity; region checks with “did‑you‑mean” hints.
- Azure region normalization (aliases → canonical).
- Azure pricing cache: `prices/azure_compute_cache_<region>.json`; `--refresh-azure-prices` bypasses cache.

---

## Installation

```bash
pip install -U click pandas boto3 requests openpyxl xlsxwriter
pip install -U azure-identity azure-mgmt-compute   # optional for Azure sizing via SDK
az login && az account set --subscription "<sub>"  # or use Azure CLI
```

---

## CLI Overview

```bash
python main.py recommend --cloud {aws|azure} --in <file.csv|.xlsx> [--region <slug>] [--strict] [--output <file>]
python main.py price      --cloud {aws|azure} (--latest | --in <recommend.csv|.xlsx>)                           [--region <slug>] [--output <file>] [--hours-per-month N]                           [--refresh-azure-prices] [--no-monthly]
```

Helpers:
```bash
python main.py list-aws-regions
python main.py list-azure-regions
```

---

## Quick Start

**AWS**
```bash
python main.py recommend --cloud aws --region us-east-1 --in servers.csv
python main.py price --cloud aws --latest --region us-east-1
```

**Azure**
```bash
az login
az account set --subscription "<sub>"
python main.py recommend --cloud azure --in servers.csv
python main.py price --cloud azure --latest
```

Outputs per run:
```
output/YYYY-MM-DD/HHMMSS/
  recommend.csv
  validator_report.csv
  price.csv
```

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

---

### Environments

If your input includes an `environment` (or `env`) column (e.g., `Production`, `Development`, `Test`, `QA`),
the **price** command will additionally create an Excel workbook with:

- **All** — every priced row
- **One sheet per environment** — filtered rows for each environment value
- **Summary** — run totals (if `summary.csv` exists)

**How to produce the workbook:** simply run `price` as usual; the app writes CSV **and** a companion XLSX next to it:
```bash
python main.py price --cloud azure --in samples/recommend_azure_with_env.csv
# writes output/.../price.csv and output/.../price.xlsx (All + per-environment tabs)
```

**Notes**
- Sheet names are sanitized for Excel (max 31 chars, invalid characters replaced).
- If there is no environment column, only **All** (and **Summary**, if present) will be written.

## Roadmap

- Azure price cache TTL (auto‑refresh stale)
- Azure preflight checks (CLI/login/subscription/provider)
- Per‑run `summary.csv` with totals/averages
- Excel report polish (styled Results + Summary sheets)
- Multi‑cloud comparison output
- CI workflow (pytest + ruff + black; golden tests)

---

## License

MIT

---

## Author

**Erick Perales** — IT Architect | Cloud Migration Specialist
https://github.com/peralese

