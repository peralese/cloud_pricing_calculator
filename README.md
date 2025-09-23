# Cloud Pricing Calculator

A Python 3.10+ CLI that validates inputs, recommends instance types, and produces detailed monthly pricing for **AWS** and **Azure** — including EC2/VM, EBS/storage, network, and **RDS** (when DB fields are provided). Outputs CSV and an Excel workbook with a clean **Executive Summary**.

---

## ✅ What changed today (2025‑09‑22)

- **RDS SQL Server pricing fixed** — we now correctly create the AWS *Pricing* client and resolve prices even when `db_instance_class` is auto‑derived.
- **Configurable Pricing endpoint** — set `AWS_PRICING_REGION` (defaults to `us-east-1`). The chosen endpoint does **not** force your priced region; region is still selected via the Price List **location** filter.
- **DB class auto‑derivation improved** — when `db_instance_class` is blank, we derive it from the compute recommendation (e.g., `m7i.2xlarge → db.m7i.2xlarge`) and apply family fallbacks when RDS doesn’t offer the derived class (e.g., SQL Server: `m7i → m6i`, `r7i → r6i`, and for compute‑only families like `c*` we map to a compatible `m*`).
- **New output column**: `resolved_db_instance_class` shows what class was actually used for pricing.
- **CSV robustness** — writer now builds fieldnames from the **union** of all rows so columns like `resolved_db_instance_class` don’t crash CSV export.
- **EBS guidance** — AWS charges for **root** EBS volumes too. If your data model only captures `ebs_gb`, make sure it **includes root GB** when you want root‑only machines to price EBS correctly (or enable separate root/data pricing in code as described below).

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
- `output/YYYY-MM-DD/HHMMSS/price.xlsx` — workbook with tabs:
  - **ExecutiveSummary** (roll-ups for EC2/VM, EBS, RDS, etc.)
  - **All** (full row data)
  - **Development**, **Production**, **Summary**, **Baseline** (as applicable)

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
