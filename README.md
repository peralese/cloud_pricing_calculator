# Cloud Pricing Calculator

A Python tool that **recommends instance/VM sizes**, **prices workloads**, and includes **AWS VPC Overhead (Baseline)** costs that roll up into the Excel **ExecutiveSummary** (with monthly and annual totals).

- ✅ **AWS**: Recommend + live pricing (AWS Pricing API) + optional **RDS** (Multi‑AZ supported)
- ✅ **Azure**: Recommend (CLI/SDK/cache) + pricing (Retail Prices API with local cache + overrides)
- ✅ **Validator** with region rules and a report
- ✅ **Baseline (AWS)**: Prompt‑driven capture of Transit Gateway & PrivateLink overhead; integrated into `price.xlsx` **ExecutiveSummary** as **“AWS VPC Overhead (Baseline)”**

Outputs land in **date/time‑stamped run folders** under `./output/`.

---

## What’s New (today)

- **Baseline (AWS)** prompt flow
  - Command: `python main.py baseline --cloud aws`
  - Prompts for TGW attachments, TGW data (GB), core Interface Endpoints per AZ (default **8**), extra endpoints per AZ, AZ count (default **2**), and endpoint data (GB).
  - **Defaults**: attachments=**1**, TGW data=**100 GB**, base endpoints/az=**8**, AZs=**2**; endpoint data defaults to the TGW GB.
  - Baseline pricing from JSON overrides → ENV → safe defaults.
- **ExecutiveSummary update**
  - Adds a row **“AWS VPC Overhead (Baseline)”** with **Monthly** and **Annual** cost.
  - Total row recomputes to include baseline.
- **Run‑folder alignment**
  - `baseline` now writes `baseline.csv` into the **same folder as the latest recommendation**, so `price --latest` picks it up automatically.
- **Workbook**
  - Adds a **Baseline** sheet with itemized baseline rows.
- **Summary files**
  - `summary.csv`/`summary.json` include `monthly_baseline_total` and `monthly_grand_total_including_baseline` when baseline exists.

---

## Installation

```bash
python -m venv .venv && . .venv/Scripts/activate   # (Windows) or 'source .venv/bin/activate' on macOS/Linux
pip install -U click pandas boto3 requests openpyxl xlsxwriter
pip install -U azure-identity azure-mgmt-compute     # optional for Azure sizing via SDK
# Azure login (for Azure runs):
az login && az account set --subscription "<subscription>"
```

> If you use Excel outputs, ensure `pandas`, `openpyxl`, and `xlsxwriter` are installed.

---

## CLI

```bash
python main.py recommend --cloud {aws|azure} --in <file.csv|.xlsx> [--region <slug>] [--strict] [--output <file>]
python main.py baseline   --cloud aws
python main.py price      --cloud {aws|azure} (--latest | --in <recommend.csv|.xlsx>) [--region <slug>] [--output <file>] [--hours-per-month N] [--refresh-azure-prices] [--no-monthly]
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
# 1) Recommend instance sizes
python main.py recommend --cloud aws --region us-east-1 --in servers.csv

# 2) Capture AWS VPC overhead (baseline) — writes to same folder as latest recommend
python main.py baseline --cloud aws

# 3) Price recommendations (folds in baseline automatically)
python main.py price --cloud aws --latest --region us-east-1
```
Open the run’s `price.xlsx` → **ExecutiveSummary** tab to see the **AWS VPC Overhead (Baseline)** line with Monthly + Annual, and the recomputed Total. A **Baseline** tab contains itemized rows.

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
  baseline.csv
  price.csv
  price.xlsx   (All, per-env, Summary, ExecutiveSummary, Baseline)
  summary.csv
  summary.json
  summary_top5.csv
```

---

## Validator Summary

- **Tier‑A (recommendation gate)** — `cloud`, `region`, and at least one of `vcpu` / `memory_gib` (>0)
- **Tier‑B (pricing gate)** — `os`, `purchase_option`, `root_gb`, `root_type`
- Row statuses: `ok`, `rec_only`, `error`
- `--strict` → non‑zero exit on any `rec_only`/`error`

---

## Input Columns

Minimum (for recommendation):
```
cloud,region,vcpu,memory_gib
```

For pricing add:
```
os,purchase_option,root_gb,root_type
```

Optional (priced when present):
```
license_model, ebs_gb, ebs_type, s3_gb, network_profile,
db_engine, db_instance_class, multi_az
```

---

## Baseline Pricing Sources

Priority:
1. `prices/aws_vpc_baseline.json` (per‑region override)
2. Environment variables: `TGW_ATTACHMENT_HOURLY`, `TGW_DATA_GB`, `VPCE_IF_HOURLY`, `VPCE_DATA_GB`
3. Safe built‑ins (documented in code)

---

## Notes & Tips

- **BYOL** for compute is priced as Linux base; license costs are not double‑counted.
- **Azure SQL Option B**, **Cosmos (RU)**, and **DynamoDB (provisioned)** supported via heuristic/override models.
- Use `--hours-per-month` to tune monthly math (default **730**).

---

## License

MIT

---

## Author

**Erick Perales** — IT Architect | Cloud Migration Specialist  
<https://github.com/peralese>
