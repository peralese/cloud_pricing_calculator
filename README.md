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
- Azure pricing cache: `prices/azure_compute_cache_<region>.json`; auto-refresh with `--azure-cache-ttl-days` (default 7); `--refresh-azure-prices` forces refresh.

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
python main.py price      --cloud {aws|azure} (--latest | --in <recommend.csv|.xlsx>)                           [--region <slug>] [--output <file>] [--hours-per-month N]                           [--refresh-azure-prices] [--azure-cache-ttl-days N] [--no-monthly]
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