# Cloud Pricing Calculator

A Python 3.10+ CLI that validates inputs, recommends instance types, and produces detailed monthly pricing for **AWS** and **Azure** — including EC2/VM, EBS/storage, network, and **RDS** (when DB fields are provided). Outputs CSV and an Excel workbook with a clean **Executive Summary**.

---

## ✅ Recent Changes

- (Oct 2025) Global tracking workbook prompt after pricing, with upsert by Application Name and updated schema (Linux/Windows monthly, storage, network, baseline).
- (Oct 2025) Baseline prompt now includes “Number of Environments?” and sets the default for “Base Interface Endpoints per AZ (core services)” to `8 × environments`.

### 2025‑09‑22

- **RDS SQL Server pricing fixed** — we now correctly create the AWS *Pricing* client and resolve prices even when `db_instance_class` is auto‑derived.
- **Configurable Pricing endpoint** — set `AWS_PRICING_REGION` (defaults to `us-east-1`). The chosen endpoint does **not** force your priced region; region is still selected via the Price List **location** filter.
- **DB class auto‑derivation improved** — when `db_instance_class` is blank, we derive it from the compute recommendation (e.g., `m7i.2xlarge → db.m7i.2xlarge`) and apply family fallbacks when RDS doesn’t offer the derived class (e.g., SQL Server: `m7i → m6i`, `r7i → r6i`, and for compute‑only families like `c*` we map to a compatible `m*`).
- **New output column**: `resolved_db_instance_class` shows what class was actually used for pricing.
- **CSV robustness** — writer now builds fieldnames from the **union** of all rows so columns like `resolved_db_instance_class` don’t crash CSV export.
- **EBS guidance** — AWS charges for **root** EBS volumes too. If your data model only captures `ebs_gb`, make sure it **includes root GB** when you want root‑only machines to price EBS correctly (or enable separate root/data pricing in code as described below).

---

## 🔹 Update: Global Tracking Sheet Integration (October 2025)

A new interactive global tracking workbook (`output/tracking.xlsx`) can capture key results from each pricing run.

### 🧭 Flow

After a successful `price` run, you’ll be prompted to add results to the global tracker:

```text
Add results to global tracking sheet? (y/n): y
What is the Application Name?: PayrollApp
App already exists — overwrite entry? (y/n): y
What is the ESATS ID?: ESATS-9921
What is the ECS #: ECS-56789
Updated tracking workbook → output/tracking.xlsx
```

### 📊 Tracked Columns

The sheet `Tracking` contains one row per application. Fields populated after each pricing run:

- `Application Name` (prompt)
- `ESATS ID` (prompt)
- `ECS #` (prompt)
- `Linux VM (Generic)` (auto; monthly cost from exec summary → sum of `monthly_compute_usd` where `os == 'linux'`)
- `Windows VMs` (auto; monthly cost from exec summary → sum of `monthly_compute_usd` where `os == 'windows'`)
- `Block Storage (EBS/Managed Disk)` (auto; monthly cost → sum of `monthly_ebs_usd`)
- `Network (egress/DTO)` (auto; monthly cost → sum of `monthly_network_usd`)
- `AWS VPC Overhead (Baseline)` (auto; monthly baseline from `baseline.csv`, if present)
- `Previously Hosted` (blank)
- `Savings Due to Modernization` (blank)

Additional details:
- Idempotent upsert keyed by Application Name (case-insensitive). If the app exists, you’ll be asked to confirm overwrite.
- The workbook and sheet are created if missing. Writes use a safe replace mode and retry briefly if the file is locked.
- The `os` column is preserved in pricing output, enabling accurate Linux/Windows counts.

---

## Quickstart

```bash
# 1) Create venv & install
python -m venv .venv && . .venv/Scripts/activate  # Windows
python -m pip install -U pip

# If using uv (preferred)
#   uv venv && . .venv/Scripts/activate
#   uv pip install -e .

pip install -r requirements.txt  # if present
# or: pip install -e .  # if project is packaged

# 2) Validate input
python main.py validate --in input.csv

# 3) Recommend instance types
python main.py recommend --cloud aws --in input.csv

# 4) Price using the latest recommendation
python main.py price --cloud aws --latest
# or explicitly
python main.py price --cloud aws --in output\YYYY-MM-DD\HHMMSS\recommend.csv
```

Azure is similar: `--cloud azure` on `recommend` and `price`.

---

## Configuration

Create a `.env` (or set real env vars):

```dotenv
# AWS credentials/profile (any standard SDK method works)
AWS_PROFILE=default
AWS_DEFAULT_REGION=us-east-1

# Pricing service endpoint region (controls *where* the pricing API lives,
# NOT the target region you are pricing). Defaults to us-east-1 if unset.
AWS_PRICING_REGION=us-east-1
```

> We use the Pricing service’s **location** filter to select the priced region (e.g., “US West (N. California)”), so using `us-east-1` for the Pricing **endpoint** does not force your prices to `us-east-1`.

---

## Input schema (minimum useful columns)

| Column | Required | Notes |
|---|---|---|
| `id` | ✅ | Unique row id |
| `cloud` | ✅ | `aws` or `azure` |
| `region` | ✅ | e.g., `us-west-1` |
| `vcpu`, `memory_gib` | ✅ for recommendation | Used by recommender when `profile` not given |
| `profile` | optional | If provided (e.g., `general_purpose`), may guide recommendation |
| `os` | optional | `linux`/`windows` |
| `license_model` | optional | For AWS RDS SQL Server: use `AWS`/`LicenseIncluded`. BYOL is not priced. |
| `ebs_gb`, `ebs_type` | optional | If root-only servers exist and you don’t price root separately, **include root GB here** |
| `db_engine` | optional | Triggers RDS pricing when present; examples: `postgres`, `mysql`, `mariadb`, `oracle`, `sql server`, `aurora postgres`, `aurora mysql` |
| `db_instance_class` | optional | If blank, we **derive** from compute reco and apply family fallbacks |

> If both `db_instance_class` **and** `recommended_instance_type` are blank, DB pricing cannot be inferred from vCPU/RAM yet and will be `0.00` (by design).

---

## Outputs

- `output/YYYY-MM-DD/HHMMSS/price.csv` — flat data with computed columns, including:
  - `price_per_hour_usd`, `monthly_compute_usd`, `monthly_ebs_usd`, `monthly_db_usd`, `monthly_total_usd`
  - `resolved_db_instance_class` (new)
  - optional `pricing_note` if you enable diagnostics in code
- `output/YYYY-MM-DD/HHMMSS/price.xlsx` - workbook with tabs:
  - **ExecutiveSummary** (roll-ups for EC2/VM, EBS, RDS, etc.)
  - **All** (full row data)
  - **Development**, **Production**, **Summary**, **Baseline** (as applicable)

---

**Baseline (AWS VPC) Costs**
- Components: `TGW Attachment`, `TGW Data`, `Interface Endpoint`, `Interface Endpoint Data`, `GitRunner EC2`, `GitRunner EBS (OS)`, `Terraform Backend S3`, and `TOTAL`.
- Default rates (USD):
  - Networking: `tgw_attachment_hourly=0.06` $/attachment-hour, `tgw_data_gb=0.02` $/GB, `vpce_if_hourly=0.01` $/endpoint-hour, `vpce_data_gb=0.01` $/GB.
  - Storage: `ebs_gp3_gb_month=0.08`, `s3_std_gb_month=0.023` (reused from pricing constants). All configurable via env vars. Per-region networking overrides supported via `prices/aws_vpc_baseline.json`.
- Hours per month: `730`.
- Formulas:
  - TGW attachments: `tgw_attachments * tgw_attachment_hourly * hours_per_month`
  - TGW data: `tgw_data_gb * tgw_data_gb_rate`
  - Interface endpoints: `(vpce_base_per_az + vpce_extra_per_az) * vpce_azs * vpce_if_hourly * hours_per_month`
  - Interface endpoint data: `vpce_data_gb * vpce_data_gb_rate`
  - GitRunner EC2 (Linux On-Demand): `gitrunner_count * ec2_price_per_hour(instance_type, region) * hours_per_month`.
    - Pricing sourced via AWS Pricing API; fallback override: set `GITRUNNER_HOURLY`.
  - GitRunner EBS (OS): `gitrunner_count * gitrunner_os_gb * ebs_gp3_gb_month`
  - Terraform Backend S3: `tf_backend_s3_gb * s3_std_gb_month`
- Prompt defaults: `tgw_attachments=1`, `tgw_data_gb=100`, `Number of Environments=1`, `vpce_base_per_az=8 × environments`, `vpce_extra_per_az=0`, `vpce_azs=2`, `vpce_data_gb` defaults to `tgw_data_gb`, `gitrunner_instance_type=t3.medium`, `gitrunner_count=1`, `gitrunner_os_gb=256`, `tf_backend_s3_gb=1`.

Enhancement: Number of Environments and dynamic VPCE default
- Add a new prompt before VPCE questions: `Number of Environments?` (numeric input).
- Use this value to compute the default for `Base Interface Endpoints per AZ (core services)` as `8 × <Number of Environments>`.
  - Example: if Number of Environments = `2`, the default shown becomes `16`.
- Users can still override this value; the computed number is only the suggested default.
- Example (networking defaults only, region-agnostic): TGW attach `1*0.06*730=43.80`, TGW data `100*0.02=2.00`, VPCE attach `16*0.01*730=116.80`, VPCE data `100*0.01=1.00` → subtotal `163.60`. Add GitRunner EC2/EBS and Terraform S3 per formulas above (EC2 hourly varies by region).
- How to run: `python main.py baseline --cloud aws` (writes `baseline.csv` to the current run folder). The price command will also auto‑prompt baseline if missing. Summary roll‑up includes the baseline total when present.

---

## RDS pricing behavior (AWS)

- If `db_engine` is present, we price RDS **only when** we have: engine + class + region.
- When `db_instance_class` is **blank**, we derive from the compute recommendation (e.g., `m7i.2xlarge → db.m7i.2xlarge`) and:
  - SQL Server: try `db.m7i.*`/`db.r7i.*` first, then fall back to `db.m6i.*`/`db.r6i.*` if the 7-series isn’t offered in your region.
  - For compute‑only families like `c*`, we map to a compatible `m*` family for DB pricing.
- For **SQL Server**, only **License Included** is priced by the tool; BYOL rows are skipped (priced `0.00`).

**Tip:** Check the **All** tab for `resolved_db_instance_class` to see exactly what was used.

---

## EBS pricing behavior (AWS)

- EBS charges apply to **root volumes** too. If your inputs only use `ebs_gb`, make sure it includes the **root size** when the server has no separate data volume.
- If you prefer separate pricing for root and data volumes, enable this snippet in `main.py`:

```python
root_gb   = as_float(r.get("root_gb"), 0.0)
root_type = (r.get("root_type") or "").strip() or (r.get("ebs_type") or "gp3")
root_cost = monthly_ebs_cost(root_gb, root_type)
data_cost = monthly_ebs_cost(as_float(r.get("ebs_gb"), 0.0), (r.get("ebs_type") or "gp3"))
r["monthly_root_ebs_usd"] = f"{root_cost:.2f}"
r["monthly_data_ebs_usd"] = f"{data_cost:.2f}"
r["monthly_ebs_usd"]      = f"{(root_cost + data_cost):.2f}"
```

---

## Troubleshooting

**DB shows $0.00 but I expected a price**
1. Ensure `db_engine` and `region` are set.
2. For SQL Server, set `license_model` to `AWS` / `LicenseIncluded` (BYOL is not priced).
3. Inspect `resolved_db_instance_class` in **All**. If it starts with `db.c*`, it may be remapped; ensure your branch includes the fallback mapping.
4. Verify AWS credentials and Networking can reach the **Pricing** API.
5. (Optional) Add a diagnostic note when a lookup fails to see the reason in the CSV.

**CSV ValueError: fields not in fieldnames**
- Fixed by building `fieldnames` from the **union** of row keys and/or using `extrasaction="ignore"` in the CSV writer.

**EBS shows $0.00 on a root-only server**
- Include the root volume size in `ebs_gb` (or enable separate root/data pricing snippet above).

---

## Example commands

```bash
# Validate
python main.py validate --in samples/aws_input.csv

# Recommend + Price AWS
python main.py recommend --cloud aws --in samples/aws_input.csv
python main.py price --cloud aws --latest

# Price a specific recommendation file
python main.py price --cloud aws --in output\2025-09-22\202120\recommend.csv
```

---

## Env / Make targets (optional)

```bash
make env   # create venv & install
make fmt   # format (black, isort)
make lint  # ruff
make test  # pytest -q
make run   # example recommend/price command
```

---

## Notes / Design choices

- We default the Pricing client to `us-east-1` (configurable) because AWS exposes the Pricing API in a subset of regions. The priced **location** is still selected via filters, so you always get correct regional prices.
- We surface `resolved_db_instance_class` to make DB derivation transparent and aid debugging.
- We bias toward resilient CSV writing to prevent schema drift from breaking runs.

## License

MIT

---

## Author

**Erick Perales** — IT Architect | Cloud Migration Specialist  
<https://github.com/peralese>
