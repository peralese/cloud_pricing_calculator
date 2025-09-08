# Cloud Pricing Calculator

A Python tool that **recommends instance/VM sizes** and **prices workloads** — one cloud per run.

- ✅ **AWS**: Recommend + live pricing (AWS Pricing API)
- ✅ **Azure**: Recommend (CLI/SDK/cache) + pricing (retail API or overrides)
- ✅ **Strict, report-only validator** with **region validation** (AWS & Azure)

All outputs are timestamped and written to `./output/`.

---

## What’s New (Sep 2025)

- **Click-only CLI** (`recommend`, `price`) — no interactive prompts.
- **Validator (report-only)**:
  - Tier‑A (required to **recommend**): `cloud`, `region`, at least one of `vcpu` or `memory_gib` (>0).
  - Tier‑B (required to **price**): `os`, `purchase_option`, `root_gb`, `root_type`.
  - Handles `NaN/null/""` correctly; numeric sanity checks for `vcpu`/`memory_gib`.
  - **Region checks** with “did‑you‑mean” suggestions:
    - AWS: expects `us-east-1`, `us-west-2`, …
    - Azure: expects canonical slugs like `eastus`, `eastus2` (aliases warned or errored).
  - Outputs `validator_report_<ts>.csv`, supports `--strict`.
- **Azure region normalization** in recommender (common aliases → canonical, e.g., `US-East` → `eastus`).
- **List commands**: `list-aws-regions`, `list-azure-regions`.

---

## Features

- **Single‑cloud runs**: `--cloud {aws|azure}` required for both steps.
- **Sizing**
  - **AWS EC2**: current‑gen x86 catalog; profile‑aware (`balanced`, `compute`, `memory`); diagnostics: `overprov_vcpu`, `overprov_mem_gib`, `fit_reason`.
  - **Azure VM**: sizes via **Azure CLI** or **Azure SDK** with local **cache** fallback; canonical region handling.
- **Pricing**
  - **AWS**: On‑Demand hourly via Pricing API + monthly breakdown (compute, EBS, S3, network, RDS).
  - **Azure**: Retail Prices API with local cache and **per‑SKU overrides** (JSON) — produces hourly & monthly totals.
- **Outputs**
  - `recommend_<ts>.csv` — recommendations & diagnostics
  - `price_<ts>.csv` — hourly + monthly totals
  - `validator_report_<ts>.csv` — row status and fix hints

---

## Installation

```bash
# In your virtual environment
pip install click pandas boto3 requests openpyxl xlsxwriter

# (Optional) Azure sizing via SDK
pip install azure-identity azure-mgmt-compute
# Or use Azure CLI (recommended for speed): az login
```

> Windows tip (PowerShell): `.\.venv\Scripts\Activate.ps1`

---

## Quick Start

### 1) Recommend

**AWS**
```bash
python main.py recommend --cloud aws --region us-east-1 --in servers.csv
```

**Azure**
```bash
# Either login with CLI:
az login
az account set --subscription "<subscription id or name>"

# Then run recommend:
python main.py recommend --cloud azure --in servers.csv
# (Per-row 'region' preferred; if you pass --region, use canonical like 'eastus')
```

The command will print something like:
```
Validation: rows=120 | ok=92 | rec_only=21 | error=7
Wrote recommendations -> output/recommend_YYYYMMDD-HHMMSS.csv
Wrote validator report -> output/validator_report_YYYYMMDD-HHMMSS.csv
```

### 2) Price

**Use the newest recommend file**
```bash
python main.py price --cloud aws   --latest --region us-east-1
python main.py price --cloud azure --latest
```

**Or point to a specific file**
```bash
python main.py price --cloud azure --in output/recommend_YYYYMMDD-HHMMSS.csv
```

---

## Validator (How it Works)

- **Tier‑A (recommendation gate)** — blocks recommendation if missing/invalid:
  - `cloud ∈ {aws, azure}`
  - `region` (canonical; Azure aliases are warned; bad regions error)
  - `vcpu` **or** `memory_gib` present and **> 0** (prefer both)

- **Tier‑B (pricing gate)** — allows recommendation but **skips pricing** if missing:
  - `os ∈ {linux, windows}` (case-insensitive)
  - `purchase_option ∈ {ondemand, spot, reserved}`
  - `root_gb`, `root_type`

**Statuses per row**
- `ok` — recommend + price
- `rec_only` — recommend only (pricing blocked); `pricing_note` explains missing fields
- `error` — dropped; see `validator_report` for fix hints

**Strict mode**
```bash
python main.py recommend --cloud azure --in servers.csv --strict
# Fails (non-zero) if any row is rec_only or error
```

**Report file**
- `validator_report_<ts>.csv` columns: `row_index,input_file,status,blocking_for,reasons,fix_hints`

---

## Input Schema (CSV/Excel)

Minimum for **recommendation**:
```
cloud,region,vcpu,memory_gib
```

Add these for **pricing**:
```
os,purchase_option,root_gb,root_type
```
Other price-impacting (optional):
```
license_model (AWS: AWS|BYOL), ebs_gb, ebs_type, s3_gb, network_profile,
db_engine, db_instance_class, multi_az
```

**Region rules**
- **AWS**: `us-east-1`, `us-east-2`, `us-west-2`, …  
- **Azure**: `eastus`, `eastus2`, `westus2`, … (lowercase, no spaces)  
  - Common aliases (e.g., `US-East`) will be normalized and **warned** by the validator; truly invalid regions **error** with suggestions.

List supported regions:
```bash
python main.py list-aws-regions
python main.py list-azure-regions
```

---

## Outputs

### Recommendations (`recommend_<ts>.csv`)
Fields include:
```
cloud,region,requested_vcpu,requested_memory_gib,profile,
recommended_instance_type,rec_vcpu,rec_memory_gib,
overprov_vcpu,overprov_mem_gib,fit_reason,note
```

### Pricing (`price_<ts>.csv`)
Fields include:
```
provider,price_per_hour_usd,
monthly_compute_usd,monthly_ebs_usd,monthly_s3_usd,monthly_network_usd,monthly_db_usd,
monthly_total_usd,pricing_note
```

---

## Azure Pricing Sources

1) **Per-SKU overrides** — `prices/azure_compute_prices.json` (highest precedence)  
2) **Retail Prices API cache** — refreshed with `--refresh-azure-prices`  
3) **Heuristic fallback** — base hourly + OS uplift

---

## Troubleshooting

- **Azure “invalid region”**: Use canonical slugs (`eastus`, `eastus2`). Try `python main.py list-azure-regions`.
- **Azure CLI sizing fails**: Run `az account show` and `az vm list-sizes --location eastus` to verify access.
- **AWS recommend says region required**: Pass `--region us-east-1` or add `region` per row.
- **Pricing note present**: The row missed Tier‑B fields; check `validator_report_<ts>.csv` for the exact hint.
- **Excel errors**: `pip install openpyxl`; ensure the sheet exists or export as CSV.

---

## Roadmap

- [x] Click-only CLI with `recommend` + `price`
- [x] Validator (report-only) with strict mode & region validation
- [x] Azure canonical regions + alias handling
- [x] Per-SKU Azure overrides & retail pricing cache
- [ ] Multi-cloud comparison output (side-by-side)
- [ ] Excel writer: summary sheet + formatting
- [ ] Optional consolidated run summary CSV
- [ ] CI workflow (pytest on 3.10/3.11; ruff + black)

---

## License

MIT

---

## Author

**Erick Perales** — IT Architect | Cloud Migration Specialist
https://github.com/peralese

