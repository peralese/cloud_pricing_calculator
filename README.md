# Auto Instance Recommender

A Python tool that recommends AWS EC2 instance types based on CPU and memory requirements, and retrieves On-Demand pricing.  
Supports **CSV** and **Excel** input, interactive prompts for missing parameters, and outputs results into a standardized `./output/` directory with timestamped filenames.

---

## Features

- **Input formats**: Accepts `.csv` and `.xlsx`/`.xls`
- **Interactive prompts**: If no `--in` file is passed, you are prompted for a path (and Excel sheet name if needed)
- **Output management**: Results are written to `./output/` with timestamped filenames (e.g. `recommend_20250830-213045.csv`)
- **EC2 recommendations**: Matches requirements against current-generation, x86_64 instance families (balanced, compute, memory)
- **Diagnostics**: Adds transparency columns (`overprov_vcpu`, `overprov_mem_gib`, `fit_reason`) to show why a type was chosen
- **Pricing**: Fetches On-Demand hourly rates from the AWS Pricing API
- **Monthly costs**: Optionally computes `monthly_cost_usd` using configurable hours-per-month (default 730)
- **Extensible**: Architecture allows adding new checks and cost factors

---

## Usage

### Install dependencies

```bash
pip install boto3 pandas openpyxl python-dotenv
```

### Recommend instances

```bash
python auto_instance_recommender.py recommend --region us-east-1 --in apps.csv
```

Or with Excel:

```bash
python auto_instance_recommender.py recommend --region us-east-1 --in apps.xlsx --sheet Sheet1
```

If you omit `--in`, you will be prompted interactively.

### Price recommendations

```bash
# Auto-picks latest recommend_*.csv if --in omitted
python auto_instance_recommender.py price --region us-east-1
```

```bash
# Strict: fail if no recommendation file found
python auto_instance_recommender.py price --region us-east-1 --latest
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

## Output

- **Recommendations**: `./output/recommend_<timestamp>.csv`  
  Includes requested resources, profile, chosen instance, overprovision metrics, and fit reason.  

- **Pricing**: `./output/price_<timestamp>.csv`  
  Extends recommendations with:  
  - Hourly cost (`price_per_hour_usd`)  
  - Monthly cost (`monthly_cost_usd`, unless `--no-monthly` is used)  
  - Pricing notes  

---

## Roadmap

1. **Guardrails (already noted)**  
   - Add logic to flag extreme overprovisioning (e.g., >4× requested resources).  
   - Suggest alternate strategies (e.g., splitting workload, different family).  

2. **Improve pricing input defaults (done)**  
   - `price` automatically looks in the `./output` folder for the most recent recommendation file if no `--in` is provided.  
   - `--latest` flag enforces strict newest-file mode.  

3. **Server metadata awareness**  
   - Include attributes like **underlying OS** by default.  
   - Use OS to influence pricing (Linux vs Windows).  

4. **Additional cost dimensions**  
   Recommend adding checks for:  
   - **Storage requirements** (EBS size/IOPS, gp3 vs io1).  
   - **Network throughput** (bandwidth tiers by instance family).  
   - **Licensing costs** (SQL Server, RHEL, SUSE).  
   - **Dedicated tenancy** pricing differences.  
   - **Spot instance pricing** as an alternative.  

5. **Monthly cost output (done)**  
   - Pricing step now computes **monthly cost** per server (default 730 hours).  
   - User can configure hours or disable monthly column.  

6. **Enhanced Excel integration**  
   - Auto-detect multiple sheets and process them in batch.  
   - Retain formatting when writing results back to Excel.  

7. **Visualization & reporting**  
   - Generate charts (CPU vs memory vs price scatterplot).  
   - Summarize costs by region or profile type.  

8. **Automation hooks**  
   - Add option to push outputs to **S3** or **Google Sheets**.  
   - Enable integration with migration tracking tools (e.g., ServiceNow export).  

---

## Example Workflow

1. Prepare an input CSV:

```csv
id,vcpu,memory_gib,profile
app-001,2,8,balanced
db-001,8,64,memory
etl-001,8,24,
```

2. Run recommender:

```bash
python auto_instance_recommender.py recommend --region us-east-1 --in apps.csv
```

3. Run pricer:

```bash
python auto_instance_recommender.py price --region us-east-1
```

---

## 📌 License

MIT License. Use freely, modify, and share.

---

## 👨‍💻 Author

**Erick Perales**  
IT Architect | Cloud Migration Specialist  
https://github.com/peralese
