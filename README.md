# Cloud Pricing Calculator

A Python tool that **recommends instance/VM sizes** and **prices workloads** for a single cloud per run.

- ✅ **AWS**: Recommend + live pricing (AWS Pricing API)
- ✅ **Azure**: Recommend (SDK/CLI/cache) + **basic pricing** (heuristic with optional overrides)
- 🔜 **Azure live pricing** via Retail Prices API (cached for speed)

The app accepts **CSV/Excel** inputs, prompts for missing info, and writes timestamped results under `./output/`.

---

## Features

- **Single-cloud runs (enforced)**: Every command requires `--cloud {aws|azure}`. The output carries that cloud forward to pricing.
- **Input formats**: `.csv`, `.xlsx`/`.xls` (sheet prompt if omitted).
- **Output management**: Timestamped files (e.g., `recommend_20250901-213045.csv`, `price_20250901-213142.csv`).
- **Sizing/recommendations**
  - **AWS EC2**: Current-gen x86_64 catalog; profile-aware (`balanced`, `compute`, `memory`); overprovision guardrails + `fit_reason`.
  - **Azure VM**: Live sizes via **Azure SDK** or **Azure CLI**, with **local cache** fallback.
- **Pricing**
  - **AWS**: On-Demand hourly via **AWS Pricing API**, plus monthly breakdowns (compute, EBS, S3, network, RDS).
  - **Azure**: **Basic model** (base + OS uplift) or **per-SKU overrides** via `prices/azure_compute_prices.json`.
- **Monthly cost breakdown**: `monthly_compute_usd`, `monthly_ebs_usd`, `monthly_s3_usd`, `monthly_network_usd`, `monthly_db_usd`, `monthly_total_usd`.

---

## Installation

```bash
# Inside your project venv
pip install boto3 pandas openpyxl
# (optional, enables Azure SDK path)
pip install azure-identity azure-mgmt-compute
```

> Windows/VS Code tip: Use the VS Code integrated terminal and activate the venv:
> `.\.venv\Scripts\Activate.ps1`

---

## Quick Start — AWS

**Recommend**
```bash
python main.py recommend --cloud aws --region us-east-1 --in servers.csv
```

**Price**
```bash
python main.py price --cloud aws --latest --hours-per-month 730
```

Notes:
- `--latest` picks the newest `recommend_*.csv` automatically.
- You can pass `--in <path>` instead of `--latest`.

---

## Quick Start — Azure

You can use **SDK** or **CLI** (or a local **cache**) for sizing.

### Option A — Azure CLI (fastest)
```bash
az login
az account set --subscription "<SubscriptionId or Name>"
python main.py recommend --cloud azure --region eastus --in servers.csv
```

### Option B — Azure SDK
```bash
pip install azure-identity azure-mgmt-compute
$env:AZURE_SUBSCRIPTION_ID="<SubscriptionId GUID>"
python main.py recommend --cloud azure --region eastus --in servers.csv
```

### Option C — Offline cache (no login)
Create `./cache/azure_vm_sizes_eastus.json` with entries like:
```json
[
  {"name":"Standard_D2s_v5","vcpu":2,"memory_gib":8},
  {"name":"Standard_D4s_v5","vcpu":4,"memory_gib":16}
]
```
Then:
```bash
python main.py recommend --cloud azure --region eastus --in servers.csv
```

**Price (Azure)**
```bash
python main.py price --cloud azure --latest --hours-per-month 730
```

> ℹ️ Azure pricing currently uses a **basic model** (base hourly + OS uplift).  
> To pin specific SKUs/regions to real numbers, add `prices/azure_compute_prices.json`:
> ```json
> [{"region":"eastus","sku":"Standard_D4s_v5","os":"linux","license_model":"BYOL","hourly":0.218}]
> ```

---

## CLI & Behavior

- `--cloud {aws|azure}` is **required** on both `recommend` and `price`.
- The recommender **stamps** the selected cloud onto every output row.
- The pricer **verifies** the file matches the CLI cloud (older files without a `cloud` column inherit the CLI flag).

Common flags:
- `--in <file>`: input CSV/Excel
- `--sheet <name>`: Excel sheet (omit to be prompted)
- `--latest`: use newest `./output/recommend_*.csv` (pricing)
- `--hours-per-month <float>`: default 730
- `--no-monthly`: skip monthly columns

---

## Input Schema

**Required**:
- `id` — row identifier
- `vcpu`
- `memory_gib`
- `profile` — `balanced` | `compute` | `memory` (can be inferred)

**Optional (pricing)**:
- `os` — `Linux` | `Windows` | `RHEL` | `SUSE`
- `license_model` — `AWS` (license-included) | `BYOL`
- `ebs_gb`, `ebs_type` — `gp3` | `io1`
- `s3_gb`
- `network_profile` — `Low` | `Medium` | `High`
- `db_engine`, `db_instance_class`, `multi_az`
- `region` — e.g., `us-east-1` (AWS) or `eastus` (Azure)

---

## Output

**Recommendations** (`./output/recommend_<timestamp>.csv`)
- `cloud`, `region`
- `recommended_instance_type` (AWS) / `instanceType` (Azure output column unified as `recommended_instance_type`)
- Diagnostics: `overprov_vcpu`, `overprov_mem_gib`, `fit_reason`, `note`

**Pricing** (`./output/price_<timestamp>.csv`)
- `price_per_hour_usd`
- Monthly breakdown: `monthly_compute_usd`, `monthly_ebs_usd`, `monthly_s3_usd`,
  `monthly_network_usd`, `monthly_db_usd`, `monthly_total_usd`
- `pricing_note` (if any filters/inputs prevented live pricing)

---

## Region tips

**AWS → Azure** rough equivalents:

| AWS            | Azure     |
|----------------|-----------|
| `us-east-1`    | `eastus`  |
| `us-east-2`    | `eastus2` |
| `us-west-1`    | `westus`  |
| `us-west-2`    | `westus2` |

Azure short codes are lowercase with no spaces (e.g., `eastus`).  
The tool normalizes common variants like “East US” → `eastus`.

---

## Troubleshooting

- **Azure: “VM sizes unavailable”**  
  - Ensure `az account show` works in the same terminal.
  - Try `az vm list-sizes --location eastus -o table`.
  - If running via SDK, set: `$env:AZURE_SUBSCRIPTION_ID="<GUID>"`.
  - As a fallback, add `cache/azure_vm_sizes_eastus.json` (see Quick Start — Azure).

- **AWS region required**  
  Pass `--region us-east-1` or set `AWS_REGION`.

- **Excel read errors**  
  `pip install pandas openpyxl`, ensure the sheet name is correct or omit `--sheet` to be prompted.

---

## Roadmap

- [x] Guardrails: overprovision flags + `fit_reason`
- [x] Pricing defaults: `--latest`, sane fallbacks
- [x] Server metadata: OS + BYOL
- [x] Cost dimensions: EBS, S3, network, RDS
- [x] Monthly output: full breakdown
- [x] **Azure sizing**: CLI/SDK with cache
- [x] **Azure basic pricing**: base + OS uplift; JSON overrides
- [ ] **Azure live pricing**: Retail Prices API + local cache
- [ ] Enhanced Excel integration (multi-sheet, formatting)
- [ ] Visualization & reporting (charts/dashboards)
- [ ] Automation hooks (S3 / Google Sheets, CMDB)

---

## License

MIT License

---

## Author

**Erick Perales**  
IT Architect | Cloud Migration Specialist  
https://github.com/peralese
