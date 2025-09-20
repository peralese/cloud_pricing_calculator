# Cloud Pricing Calculator

A Python tool that **recommends instance/VM sizes** and **prices workloads** for one cloud per run (AWS or Azure).  
Outputs include a CSV and a polished Excel workbook with **All**, **per‑Environment** tabs, optional **Summary**, and an **ExecutiveSummary** that shows monthly and **annual** rollups by category.

---

## What’s New (2025‑09‑19)

- ✅ **Azure SQL DB / Managed Instance pricing (Option B)** is now wired into the CLI.  
  Uses vCores + storage/backup GB‑month. Accepts both `az_sql_*` **and** `db_*` column names.
- ✅ **ExecutiveSummary** sheet in `price.xlsx`:
  - Buckets for **Windows/RHEL/SUSE/Linux VMs**, **RDS** engines, **Azure SQL**, plus single‑line rollups for **Storage** and **Network**.
  - Shows **Per‑Unit Monthly**, **Monthly Cost**, and **Annual Cost**, with a **Total** line.
- ✅ Sample bulk datasets for quick testing:
  - **Azure**: `test_azure_bulk.csv`  
  - **AWS**: `test_aws_bulk.csv`
- Improvements to file layout, environment tabbing, and validator messaging.

> Annual cost is computed in the **ExecutiveSummary** sheet (monthly × 12). The CSV focuses on per‑row monthly fields for composability.

---

## Install

> Python 3.10+ recommended

```bash
# Core
pip install -U click pandas requests openpyxl xlsxwriter

# AWS pricing (EC2/RDS via Pricing API)
pip install -U boto3

# Azure (optional for sizing helpers / login)
pip install -U azure-identity azure-mgmt-compute
az login && az account set --subscription "<your-subscription>"
```

---

## CLI

```bash
python main.py recommend --cloud {aws|azure} --in <file.csv|.xlsx> [--region <aws-region>] [--strict] [--output <path>]
python main.py price      --cloud {aws|azure} (--latest | --in <recommend.csv|.xlsx>) \
                          [--region <aws-region>] [--hours-per-month 730] \
                          [--refresh-azure-prices] [--no-monthly] [--output <path>]

# Helpers
python main.py list-aws-regions
python main.py list-azure-regions
```

- **`recommend`** validates the input and proposes a size/sku (no pricing).  
- **`price`** reads a recommend file and adds pricing columns; also writes `price.xlsx` with the ExecutiveSummary.

All outputs go to a **date/time‑stamped run folder** under `./output/` unless you pass explicit paths.

---

## Quick Start

**Azure**

```bash
python main.py recommend --cloud azure --in test_azure_bulk.csv
python main.py price --cloud azure --latest
```

**AWS**

```bash
python main.py recommend --cloud aws --in test_aws_bulk.csv --region us-east-1
python main.py price --cloud aws --latest --region us-east-1
```

**Sample datasets** (ready to use):
- Azure: `test_azure_bulk.csv`
- AWS: `test_aws_bulk.csv`

---

## Input Schema

At minimum for recommendation:

| Column         | Example        | Notes                                  |
|----------------|----------------|----------------------------------------|
| `cloud`        | `aws`/`azure`  | One cloud per run                      |
| `region`       | `us-east-1`    | Azure uses its region slugs (e.g. `eastus`) |
| `vcpu`         | `4`            |                                        |
| `memory_gib`   | `16`           |                                        |

For per‑row compute pricing:

| Column            | Example         | Notes                                           |
|-------------------|-----------------|-------------------------------------------------|
| `os`              | `Linux`         | `Windows`/`Linux`/`RHEL`/`SUSE`                 |
| `license_model`   | `AWS`           | AWS: `AWS` or `BYOL`; Azure SQL: `LicenseIncluded` or `AHUB` |
| `purchase_option` | `OnDemand`      | Currently modeled as on‑demand                  |
| `root_gb`         | `64`            |                                                 |
| `root_type`       | `gp3`           |                                                 |
| `ebs_gb`          | `500`           | Block storage GB‑month (EBS / Managed Disk)     |
| `ebs_type`        | `gp3`           | `gp3` or `io1` mapping                          |
| `s3_gb`           | `100`           | Object storage GB‑month (S3 / Blob)             |
| `network_profile` | `low`           | `low` / `medium` / `high` egress profile        |

### Databases

**AWS RDS** (priced via AWS Pricing API):
| Column               | Example          |
|----------------------|------------------|
| `db_engine`          | `PostgreSQL`     |
| `db_instance_class`  | `db.t3.medium`   |
| `multi_az`           | `true/false`     |

> RDS on‑demand price includes the standard automated backup retention baseline; extra backup (beyond baseline), cross‑region copies, and manual snapshot storage are **not** modeled yet.

**Azure SQL DB / Managed Instance** (now fully wired into CLI):
- Accepts either **`az_sql_*`** *or* **`db_*`** names (aliases).  
- Uses `db_storage_gb` (shared name) for the storage/backup component.

| Aliases (accepted)                   | Meaning                               | Example            |
|-------------------------------------|----------------------------------------|--------------------|
| `az_sql_deployment` \| `db_deployment` | `single` (Single DB) or `mi` (Managed Instance) | `single` |
| `az_sql_tier` \| `db_tier`          | Service tier                            | `GeneralPurpose` / `BusinessCritical` / `Hyperscale` |
| `az_sql_family` \| `db_family`      | Hardware family (optional; overrides)   | `Gen5`             |
| `az_sql_vcores` \| `db_vcores`      | vCores                                  | `8`                |
| `az_sql_storage_gb` \| `db_storage_gb` | GB‑month for data+backup (modeled)      | `500`              |
| `az_sql_license_model` \| `license_model` | `LicenseIncluded` or `AHUB` (Hybrid Benefit) | `AHUB`       |

**AHUB (Azure Hybrid Benefit)**: If you bring SQL licenses (with Software Assurance/subscription), compute is discounted. In our heuristic, the **compute portion** gets `AZSQL_AHUB_DISCOUNT` (default 0.25); storage/backup is not discounted.

---

## Pricing Model & Tunables

Environment variables (override defaults):

```bash
# Storage / DTO
S3_STD_GB_MONTH=0.023
EBS_GP3_GB_MONTH=0.08
EBS_IO1_GB_MONTH=0.125
DTO_GB_PRICE=0.09
NETWORK_EGRESS_GB_LOW=50
NETWORK_EGRESS_GB_MED=500
NETWORK_EGRESS_GB_HIGH=5000

# Azure SQL heuristics (when no JSON override)
AZSQL_DB_VCORE_HOURLY_GP=0.15
AZSQL_MI_VCORE_HOURLY_GP=0.20
AZSQL_STORAGE_GB_MONTH=0.12
AZSQL_BC_MULTIPLIER=1.75
AZSQL_HS_MULTIPLIER=1.25
AZSQL_AHUB_DISCOUNT=0.25  # AHUB compute discount

# Azure VM retail cache (used automatically)
AZURE_BASE_DEFAULT_HOURLY=0.20  # fallback when live price unavailable
```

JSON overrides (optional):
- `prices/azure_compute_prices.json`
- `prices/azure_sql_prices.json`
- …provide exact `hourly` and `storage_gb_month` when you need precise parity.

Azure Retail Prices API cache for compute is stored at `prices/azure_compute_cache_<region>.json`. Use `--refresh-azure-prices` to bypass cache for a run.

---

## Outputs

A typical run folder:

```
output/YYYY-MM-DD/HHMMSS/
  recommend.csv
  validator_report.csv
  price.csv
  price.xlsx
  summary.csv          # optional (if summary.py present)
```

**CSV fields** (per row, when pricing enabled):
- `price_per_hour_usd`
- `monthly_compute_usd`, `monthly_ebs_usd`, `monthly_s3_usd`, `monthly_network_usd`, `monthly_db_usd`
- `monthly_total_usd`

**Excel workbook (`price.xlsx`)**:
- **All** — all priced rows
- **<Environment>** — one sheet per distinct `environment` value
- **Summary** — if `summary.csv` exists
- **ExecutiveSummary** — rollups with **Monthly** + **Annual** totals

ExecutiveSummary buckets:
- Compute: **Windows VMs**, **RHEL Servers**, **SUSE Servers**, **Linux VMs (generic)**
- Databases: **RDS \<engine\>** and **Azure SQL (\<tier – deployment\>)**
- Singletons: **Block Storage (EBS/Managed Disk)**, **Object Storage (S3/Blob)**, **Network (egress/DTO)**
- Each line shows **Per Unit (mo)** (where applicable), **Monthly**, **Annual**, and a **Total** row at bottom.

---

## Troubleshooting

- **No ExecutiveSummary sheet**: Ensure you’re running `price` on a produced recommend file; the writer builds this inside the Excel export.  
- **DB monthly is 0 (Azure)**: Make sure `db_deployment/db_tier/db_vcores/db_storage_gb` (or `az_sql_*`) are populated. `license_model=BYOL` maps to **AHUB** automatically for Azure SQL.  
- **No storage/network rows in summary**: Provide non‑zero `ebs_gb`, `s3_gb`, or set `network_profile` to `low|medium|high`.
- **RDS price missing**: Verify `db_engine`, `db_instance_class`, region, and that `boto3` has network access to the AWS Pricing API.

---

## Roadmap (short list)

- Azure compute cache TTL + auto‑refresh
- Optional `annual_total_usd` in CSV (in addition to ExecutiveSummary)
- More precise storage/backup modeling for AWS RDS extras
- CI: ruff/black/pytest + golden tests for pricing tables

---

## License

MIT

## Author

**Erick Perales** — IT Architect | Cloud Migration Specialist  
https://github.com/peralese
