# Cloud Pricing Calculator

A Python tool that recommends instance types and retrieves pricing for cloud workloads.  
Currently supports **AWS EC2** recommendations and pricing end-to-end, with **Azure VM** support in progress.  
Accepts **CSV** and **Excel** input, interactive prompts for missing parameters, and outputs results into a standardized `./output/` directory with timestamped filenames.

---

## Features

- **Input formats**: Accepts `.csv` and `.xlsx`/`.xls`
- **Interactive prompts**: If no `--in` file is passed, you are prompted for a path (and Excel sheet name if needed)
- **Output management**: Results are written to `./output/` with timestamped filenames (e.g. `recommend_20250901-213045.csv`)
- **AWS EC2 recommendations**: Matches requirements against current-generation, x86_64 instance families (`balanced`, `compute`, `memory`)
- **Diagnostics**: Adds transparency columns (`overprov_vcpu`, `overprov_mem_gib`, `fit_reason`) to show why a type was chosen
- **AWS Pricing**: Fetches On-Demand hourly rates from the AWS Pricing API
- **Multi-dimension monthly costs**: Computes estimates for:
  - Compute (instance hourly → monthly)
  - OS & licensing (AWS license-included vs BYOL)
  - Block storage (EBS gp3/io1, $/GB-month)
  - Object storage (S3 Standard, $/GB-month)
  - Networking (Low/Medium/High egress profiles)
  - Database (RDS engines, instance size, Multi-AZ)
- **Extensible**: Architecture split into `main.py`, `recommender.py`, and `pricing.py` for easier extension
- **Azure support**: VM recommendations and pricing coming soon

---

## Usage

### Install dependencies

```bash
pip install boto3 pandas openpyxl python-dotenv

```

### Recommend instances

```bash
python main.py recommend --cloud aws --region us-east-1 --in apps.csv
```

Or with Excel:

```bash
python main.py recommend --cloud aws --region us-east-1 --in apps.xlsx --sheet Sheet1
```

If you omit `--in`, you will be prompted interactively.

### Price recommendations (AWS)

```bash
# Auto-picks latest recommend_*.csv if --in omitted
python main.py price --cloud aws --region us-east-1

# Strict: fail if no recommendation file found
python main.py price --cloud aws --region us-east-1 --latest

# Explicit file
python main.py price --cloud aws --region us-east-1 --in ./output/recommend_20250830-213045.csv

```

```bash
# Explicit file
python auto_instance_recommender.py price --region us-east-1 --in ./output/recommend_20250830-213045.csv
```

#### Pricing options
- `--hours-per-month <float>` : Hours used to compute monthly cost (default: 730)  
- `--no-monthly` : Skip monthly cost column  
- `--latest` : Force using newest `output/recommend_*.csv` and fail if none found  

---

---

## Input Schema

The input CSV/Excel must contain at least:

- `id` – application/server identifier  
- `vcpu` – number of vCPUs  
- `memory_gib` – memory in GiB  
- `profile` – workload profile (`balanced`, `compute`, `memory`)  

Optional expanded fields for pricing:

- `os` – `Linux`, `Windows`, `RHEL`, `SUSE`  
- `license_model` – `AWS` (license-included) or `BYOL`  
- `ebs_gb` – total EBS GB (across volumes of same type)  
- `ebs_type` – `gp3`, `io1`, `st1`  
- `ebs_iops` – provisioned IOPS (gp3/io1, optional)  
- `s3_gb` – object storage GB (S3 Standard)  
- `network_profile` – `Low`, `Medium`, `High` (maps to assumed egress GB/month)  
- `db_engine` – `Postgres`, `MySQL`, `SQLServer`, etc.  
- `db_instance_class` – RDS instance class (e.g., `db.m5.large`)  
- `db_storage_gb` – DB storage GB  
- `multi_az` – `Yes`/`No`  

---
## Output

- **Recommendations**: `./output/recommend_<timestamp>.csv`  
  Includes requested resources, chosen instance, overprovision metrics, fit reason, plus all carried-through input columns.

- **Pricing**: `./output/price_<timestamp>.csv`  
  Extends recommendations with cost breakdown:  
  - `price_per_hour_usd`  
  - `monthly_compute_usd`  
  - `monthly_ebs_usd`  
  - `monthly_s3_usd`  
  - `monthly_network_usd`  
  - `monthly_db_usd`  
  - `monthly_total_usd`  
  - `pricing_note`  

---

## Roadmap


- [x] **Guardrails**  
  Flag extreme overprovisioning (e.g., >4× requested resources) and provide fit transparency (`cpu-bound`, `memory-bound`, `exact`).

- [x] **Pricing input defaults**  
  `price` automatically looks in the `./output` folder for the most recent recommendation file if no `--in` is provided.  
  `--latest` flag enforces strict newest-file mode.

- [x] **Server metadata awareness**  
  Supports OS awareness (`Linux`, `Windows`, `RHEL`, `SUSE`) and BYOL licensing model.

- [x] **Additional cost dimensions**  
  Block storage, object storage, networking, and database costs now included.

- [x] **Monthly cost output**  
  Pricing step computes **monthly cost** per server (default 730 hours).  
  User can configure hours or disable monthly columns.

- [ ] **Azure support (coming soon)**  
  Add VM sizing and pricing for Microsoft Azure.

- [ ] **Enhanced Excel integration**  
  Auto-detect multiple sheets and process them in batch.  
  Retain formatting when writing results back to Excel.

- [ ] **Visualization & reporting**  
  Generate charts (CPU vs memory vs price scatterplots).  
  Summarize costs by region, profile type, or OS.  
  Build cost breakdown dashboards.

- [ ] **Automation hooks**  
  Add option to push outputs to **S3** or **Google Sheets**.  
  Enable integration with migration tracking tools (e.g., ServiceNow, CMDB).
 
---

## Example Workflow

1. Prepare an input CSV:

```csv
id,vcpu,memory_gib,profile,os,license_model,ebs_gb,ebs_type,s3_gb,network_profile,db_engine,db_instance_class,db_storage_gb,multi_az
app-001,4,16,balanced,Windows,AWS,350,gp3,100,Medium,Postgres,db.m5.large,100,Yes
app-002,2,8,,RHEL,BYOL,50,gp3,0,Low,,,,No

```

2. Run recommender:

```bash
python main.py recommend --cloud aws --region us-east-1 --in servers.csv

```

3. Run pricer:

```bash
python main.py price --cloud aws --region us-east-1 --latest
```

## 📌 License

MIT License. Use freely, modify, and share.

---

## 👨‍💻 Author

**Erick Perales**  
IT Architect | Cloud Migration Specialist  
https://github.com/peralese
