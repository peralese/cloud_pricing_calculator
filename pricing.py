# pricing.py
import csv, sys
from pathlib import Path
from typing import Optional, List

# ---- Cost model defaults (env overrides welcome) ----
import os
S3_STD_GB_MONTH = float(os.getenv("S3_STD_GB_MONTH", "0.023"))
EBS_GP3_GB_MONTH = float(os.getenv("EBS_GP3_GB_MONTH", "0.08"))
EBS_IO1_GB_MONTH = float(os.getenv("EBS_IO1_GB_MONTH", "0.125"))
DTO_GB_PRICE = float(os.getenv("DTO_GB_PRICE", "0.09"))
NETWORK_PROFILE_TO_GB = {
    "low":   float(os.getenv("NETWORK_EGRESS_GB_LOW", "50")),
    "medium":float(os.getenv("NETWORK_EGRESS_GB_MED", "500")),
    "high":  float(os.getenv("NETWORK_EGRESS_GB_HIGH", "5000")),
}

def as_float(x, default=0.0):
    try:
        if x is None or x == "": return default
        return float(x)
    except Exception:
        return default

def as_int(x, default=0):
    try:
        if x is None or x == "": return default
        return int(float(x))
    except Exception:
        return default

def as_bool(x, default=False):
    if isinstance(x, bool): return x
    if x is None: return default
    s = str(x).strip().lower()
    if s in {"y","yes","true","1"}: return True
    if s in {"n","no","false","0"}: return False
    return default

def find_latest_output(patterns=("output/recommend_*.csv","output/recommend_*.xlsx")) -> Optional[Path]:
    from glob import glob
    candidates: List[str] = []
    for pat in patterns: candidates.extend(glob(pat))
    if not candidates: return None
    return max((Path(p) for p in candidates), key=lambda p: p.stat().st_mtime)

def prompt_for_input_path() -> Path:
    print("Enter the path to your input file (.csv, .xlsx, or .xls):")
    while True:
        raw = input("> ").strip().strip('"').strip("'")
        if not raw:
            print("Please provide a file path."); continue
        p = Path(raw).expanduser()
        if not p.exists():
            print(f"❌ File not found: {p}\nTry again:"); continue
        if p.suffix.lower() not in {".csv",".xlsx",".xls"}:
            print("❌ Unsupported file type. Please provide .csv, .xlsx, or .xls"); continue
        return p

def maybe_prompt_for_sheet(path: Path, sheet: Optional[str]) -> Optional[str]:
    if path.suffix.lower() in {".xlsx",".xls"} and not sheet:
        print("Excel file detected. Enter a sheet name (or press Enter for the first sheet):")
        s = input("> ").strip()
        return s or None
    return sheet

def read_rows(path: str, sheet: Optional[str] = None) -> List[dict]:
    p = Path(path); suffix = p.suffix.lower()
    if suffix == ".csv":
        with open(p, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    elif suffix in {".xlsx",".xls"}:
        try:
            import pandas as pd
        except ImportError:
            print("Excel input requested but pandas is missing. Install with:\n  pip install pandas openpyxl", file=sys.stderr)
            sys.exit(1)
        try:
            df = pd.read_excel(p, sheet_name=sheet if sheet is not None else 0)
        except Exception as e:
            print(f"❌ Failed to read Excel file: {e}", file=sys.stderr); sys.exit(1)
        df.columns = [str(c).strip() for c in df.columns]
        return df.to_dict(orient="records")
    else:
        print("❌ Unsupported input file format (use .csv, .xlsx, or .xls)", file=sys.stderr)
        sys.exit(1)

def write_rows(path: str, rows: List[dict], fieldnames: List[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows: w.writerow(r)

# ---- AWS Pricing ----
AWS_REGION_TO_LOCATION = {
    "us-east-1": "US East (N. Virginia)", "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)", "us-west-2": "US West (Oregon)",
    "ca-central-1": "Canada (Central)", "eu-central-1": "EU (Frankfurt)",
    "eu-central-2": "EU (Zurich)", "eu-west-1": "EU (Ireland)", "eu-west-2": "EU (London)",
    "eu-west-3": "EU (Paris)", "eu-north-1": "EU (Stockholm)", "eu-south-1": "EU (Milan)",
    "eu-south-2": "EU (Spain)", "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-south-2": "Asia Pacific (Hyderabad)", "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)", "ap-northeast-3": "Asia Pacific (Osaka)",
    "ap-southeast-1": "Asia Pacific (Singapore)", "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-southeast-3": "Asia Pacific (Jakarta)", "ap-southeast-4": "Asia Pacific (Melbourne)",
    "ap-east-1": "Asia Pacific (Hong Kong)", "sa-east-1": "South America (Sao Paulo)",
    "me-south-1": "Middle East (Bahrain)", "me-central-1": "Middle East (UAE)",
    "af-south-1": "Africa (Cape Town)",
}

def _pricing_first_usd(pl_obj: dict) -> Optional[float]:
    for term in pl_obj.get("terms", {}).get("OnDemand", {}).values():
        for dim in term.get("priceDimensions", {}).values():
            usd = dim.get("pricePerUnit", {}).get("USD")
            unit = dim.get("unit")
            if usd and unit in {"Hrs","Quantity"}:
                try: return float(usd)
                except Exception: pass
    return None

def price_ec2_ondemand(instance_type: str, region: str, os_name: str = "Linux") -> Optional[float]:
    import json, boto3
    location = AWS_REGION_TO_LOCATION.get(region)
    if not location: return None
    pricing = boto3.client("pricing", region_name="us-east-1")
    filters = [
        {"Type":"TERM_MATCH","Field":"instanceType","Value":instance_type},
        {"Type":"TERM_MATCH","Field":"location","Value":location},
        {"Type":"TERM_MATCH","Field":"operatingSystem","Value":os_name},
        {"Type":"TERM_MATCH","Field":"tenancy","Value":"Shared"},
        {"Type":"TERM_MATCH","Field":"preInstalledSw","Value":"NA"},
        {"Type":"TERM_MATCH","Field":"capacitystatus","Value":"Used"},
    ]
    resp = pricing.get_products(ServiceCode="AmazonEC2", Filters=filters, MaxResults=100)
    for pl in resp.get("PriceList", []):
        o = json.loads(pl)
        usd = _pricing_first_usd(o)
        if usd is not None: return usd
    return None

def price_rds_ondemand(engine: str, instance_class: str, region: str, license_model: str = "AWS", multi_az: bool = False) -> Optional[float]:
    import json, boto3
    location = AWS_REGION_TO_LOCATION.get(region)
    if not location: return None
    lm = "License included" if (str(license_model).strip().lower() != "byol") else "Bring your own license"
    dep = "Multi-AZ" if multi_az else "Single-AZ"
    pricing = boto3.client("pricing", region_name="us-east-1")
    filters = [
        {"Type":"TERM_MATCH","Field":"location","Value":location},
        {"Type":"TERM_MATCH","Field":"databaseEngine","Value":engine},
        {"Type":"TERM_MATCH","Field":"instanceType","Value":instance_class},
        {"Type":"TERM_MATCH","Field":"deploymentOption","Value":dep},
        {"Type":"TERM_MATCH","Field":"licenseModel","Value":lm},
    ]
    try:
        resp = pricing.get_products(ServiceCode="AmazonRDS", Filters=filters, MaxResults=100)
    except Exception:
        return None
    for pl in resp.get("PriceList", []):
        try:
            o = json.loads(pl)
        except Exception:
            continue
        usd = _pricing_first_usd(o)
        if usd is not None: return usd
    return None

# ---- Monthly calculators ----
def monthly_compute_cost(price_per_hour: Optional[float], hours: float) -> float:
    return round((price_per_hour or 0.0) * hours, 2)

def monthly_ebs_cost(ebs_gb: float, ebs_type: str = "gp3") -> float:
    et = (ebs_type or "gp3").strip().lower()
    rate = EBS_IO1_GB_MONTH if et == "io1" else EBS_GP3_GB_MONTH
    return round(max(0.0, ebs_gb) * rate, 2)

def monthly_s3_cost(s3_gb: float) -> float:
    return round(max(0.0, s3_gb) * S3_STD_GB_MONTH, 2)

def monthly_network_cost(profile: str) -> float:
    if not profile: return 0.0
    gb = NETWORK_PROFILE_TO_GB.get(profile.strip().lower())
    if gb is None: return 0.0
    return round(gb * DTO_GB_PRICE, 2)

def monthly_rds_cost(engine: str, instance_class: str, region: str, license_model: str, multi_az: bool, hours: float) -> float:
    p = price_rds_ondemand(engine, instance_class, region, license_model=license_model, multi_az=multi_az)
    return round((p or 0.0) * hours, 2)

