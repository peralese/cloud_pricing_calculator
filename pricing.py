# pricing.py
import csv, sys, json, os
from pathlib import Path
from typing import Optional, List
import time, requests
from typing import Dict, Tuple

# ---------- Cost model defaults ----------
S3_STD_GB_MONTH = float(os.getenv("S3_STD_GB_MONTH", "0.023"))
EBS_GP3_GB_MONTH = float(os.getenv("EBS_GP3_GB_MONTH", "0.08"))
EBS_IO1_GB_MONTH = float(os.getenv("EBS_IO1_GB_MONTH", "0.125"))
DTO_GB_PRICE = float(os.getenv("DTO_GB_PRICE", "0.09"))
NETWORK_PROFILE_TO_GB = {
    "low":   float(os.getenv("NETWORK_EGRESS_GB_LOW", "50")),
    "medium":float(os.getenv("NETWORK_EGRESS_GB_MED", "500")),
    "high":  float(os.getenv("NETWORK_EGRESS_GB_HIGH", "5000")),
}
# --- Add near the other Azure constants ---
AZSQL_DB_VCORE_HOURLY_GP   = float(os.getenv("AZSQL_DB_VCORE_HOURLY_GP", "0.15"))  # $/vCore-hour
AZSQL_MI_VCORE_HOURLY_GP   = float(os.getenv("AZSQL_MI_VCORE_HOURLY_GP", "0.20"))  # $/vCore-hour
AZSQL_STORAGE_GB_MONTH     = float(os.getenv("AZSQL_STORAGE_GB_MONTH", "0.12"))    # $/GB-month
AZSQL_BC_MULTIPLIER        = float(os.getenv("AZSQL_BC_MULTIPLIER", "1.75"))       # Business Critical uplift
AZSQL_HS_MULTIPLIER        = float(os.getenv("AZSQL_HS_MULTIPLIER", "1.25"))       # Hyperscale uplift
AZSQL_AHUB_DISCOUNT        = float(os.getenv("AZSQL_AHUB_DISCOUNT", "0.25"))       # 25% off compute if AHUB

def _cache_age_days(p: Path) -> float:
    try:
        return (time.time() - p.stat().st_mtime) / 86400.0
    except FileNotFoundError:
        return 1e9

# ---------- I/O ----------
def _lazy_pandas():
    try:
        import pandas as pd
        return pd
    except ImportError:
        print("Excel input requested but pandas is missing. Install with:\n  pip install pandas openpyxl", file=sys.stderr)
        sys.exit(1)

def _autosize_and_style_excel(writer, df, sheet_name: str = "Results") -> None:
    """
    Basic polish: autofilter, freeze header, auto column widths.
    Compatible with xlsxwriter engine.
    """
    try:
        ws = writer.sheets.get(sheet_name)
        if ws is None:
            return
        # Add autofilter on entire range
        nrows, ncols = df.shape
        ws.autofilter(0, 0, max(nrows - 1, 0), max(ncols - 1, 0))
        # Freeze header row
        ws.freeze_panes(1, 0)
        # Compute widths
        widths = []
        for i, col in enumerate(df.columns):
            # header width
            w = len(str(col)) + 2
            # sample first 500 rows to keep it quick
            sample = df[col].astype(str).head(500)
            if not sample.empty:
                w = max(w, sample.map(lambda s: len(s)).max() + 1)
            widths.append(min(w, 60))  # cap
        for idx, w in enumerate(widths):
            ws.set_column(idx, idx, w)
    except Exception:
        # Best-effort only
        pass

def read_rows(path: str, sheet: Optional[str] = None) -> List[dict]:
    p = Path(path); suffix = p.suffix.lower()
    if suffix == ".csv":
        with open(p, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    elif suffix in {".xlsx",".xls"}:
        pd = _lazy_pandas()
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
    p = Path(path); suff = p.suffix.lower()
    if suff in {".xlsx", ".xls"}:
        pd = _lazy_pandas()
        frame = pd.DataFrame(rows, columns=fieldnames)
        try:
            with pd.ExcelWriter(p, engine="xlsxwriter") as writer:
                frame.to_excel(writer, index=False, sheet_name="Results")
                _autosize_and_style_excel(writer, frame, "Results")
        except Exception:
            # fallback without styling
            frame.to_excel(p, index=False, sheet_name="Results")
        return
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

# ---------- AWS pricing ----------
def _lazy_boto3():
    try:
        import boto3
        return boto3
    except ImportError:
        print("This feature requires boto3. Install with: pip install boto3", file=sys.stderr)
        sys.exit(1)

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
    # ✅ GovCloud regions
    "us-gov-west-1": "AWS GovCloud (US-West)",
    "us-gov-east-1": "AWS GovCloud (US-East)",
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
    boto3 = _lazy_boto3()
    # ✅ normalize region (handles aliases like "aws govcloud us-west")
    region = normalize_region(region)
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

def _canon_rds_engine(engine: str) -> str:
    """
+    Map common CSV-friendly tokens to AWS Price List canonical databaseEngine names.
+    """
    m = {
        "postgres": "PostgreSQL",
        "postgresql": "PostgreSQL",
        "aurora-postgres": "Aurora PostgreSQL",
        "aurora-postgresql": "Aurora PostgreSQL",
        "aurora-mysql": "Aurora MySQL",
        "mysql": "MySQL",
        "mariadb": "MariaDB",
        "oracle": "Oracle",
        "sqlserver": "SQL Server",
        "sql server": "SQL Server",
    }
    e = (engine or "").strip().lower()
    return m.get(e, engine)  # fall back to given value if already canonical

# --------------------------------------------------------------------
# Helpers for GovCloud awareness (no refactor required elsewhere)
# --------------------------------------------------------------------
def is_govcloud(region: str) -> bool:
    """Return True if region is in the AWS GovCloud partition."""
    return str(region).lower().startswith("us-gov-")

def aws_partition_for_region(region: str) -> str:
    """
    Returns the AWS partition id for the region.
    Useful if you ever need to branch (e.g., Savings Plans/RI logic).
    """
    return "aws-us-gov" if is_govcloud(region) else "aws"

# (Optional) accept a few human-ish aliases and normalize to canonical codes.
_REGION_ALIAS = {
    "aws govcloud us-west": "us-gov-west-1",
    "aws-gov-west": "us-gov-west-1",
    "govcloud-us-west": "us-gov-west-1",
    "gov-west-1": "us-gov-west-1",
    "aws govcloud us-east": "us-gov-east-1",
    "aws-gov-east": "us-gov-east-1",
    "govcloud-us-east": "us-gov-east-1",
    "gov-east-1": "us-gov-east-1",
}

def normalize_region(region: str) -> str:
    r = (region or "").strip().lower()
    if r in AWS_REGION_TO_LOCATION:
        return r
    if r in _REGION_ALIAS:
        return _REGION_ALIAS[r]
    # legacy transforms like 'govcloud-us-west-1' -> 'us-gov-west-1'
    r2 = r.replace("govcloud-us", "us-gov")
    if r2 in AWS_REGION_TO_LOCATION:
        return r2
    return r  # fall through (let existing validators handle errors)

def price_rds_ondemand(engine: str, instance_class: str, region: str, license_model: str = "AWS", multi_az: bool = False) -> Optional[float]:
    boto3 = _lazy_boto3()
    # ✅ normalize region and engine
    region = normalize_region(region)
    location = AWS_REGION_TO_LOCATION.get(region)
    if not location: return None
    lm = "License included" if (str(license_model).strip().lower() != "byol") else "Bring your own license"
    dep = "Multi-AZ" if multi_az else "Single-AZ"
    pricing = boto3.client("pricing", region_name="us-east-1")
    filters = [
        {"Type":"TERM_MATCH","Field":"location","Value":location},
        {"Type":"TERM_MATCH","Field":"databaseEngine","Value":_canon_rds_engine(engine)},
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
# --- Add anywhere below your JSON helpers (e.g., near other monthly_* fns) ---
from pathlib import Path

def _load_override_json(path: Path):
    if not path.exists(): return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def monthly_azure_sql_cost(
    deployment: str,           # "single" | "mi"
    region: str,
    tier: str,                 # "GeneralPurpose" | "BusinessCritical" | "Hyperscale"
    family: str | None,        # e.g., "Gen5" (optional)
    vcores: float,
    storage_gb: float,
    license_model: str,        # "AHUB" | "LicenseIncluded"
    hours: float,
    use_overrides: bool = True,
) -> float:

    """
    1) Try prices/azure_sql_prices.json override (recommended for accuracy).
       Match on deployment, region, tier, family (optional), vcores, license_model.
    2) Otherwise, simple heuristic:
       hourly = vcores * base_vcore_rate * tier_multiplier * (AHUB discount if applicable)
       monthly = hourly * hours + storage_gb * AZSQL_STORAGE_GB_MONTH
    """
    dep = (deployment or "single").strip().lower()
    reg = (region or "eastus").strip().lower()
    tier_norm = (tier or "GeneralPurpose").strip()
    fam = (family or "").strip()
    lic = (license_model or "LicenseIncluded").strip().upper()

    # --- 1) Overrides (optional) ---
    overrides = _load_override_json(Path("prices/azure_sql_prices.json")) or []
    if use_overrides and overrides:
        for r in overrides:
            if (str(r.get("deployment","")).lower() == dep and
                str(r.get("region","")).strip().lower() == reg and
                str(r.get("tier","")) == tier_norm and
                str(r.get("license_model","LicenseIncluded")).strip().upper() == lic and
                float(r.get("vcores", vcores)) == float(vcores) and
                str(r.get("family","")).strip() == fam):
                hourly = float(r.get("hourly", 0.0))
                storage_rate = float(r.get("storage_gb_month", AZSQL_STORAGE_GB_MONTH))
                return round(hourly * float(hours) + max(0.0, float(storage_gb)) * storage_rate, 2)

    # --- 2) Heuristic path ---
    base = AZSQL_MI_VCORE_HOURLY_GP if dep == "mi" else AZSQL_DB_VCORE_HOURLY_GP
    mult = 1.0
    lt = tier_norm.replace("_", "").lower()
    if lt in {"businesscritical", "bc"}:
        mult = AZSQL_BC_MULTIPLIER
    elif lt in {"hyperscale", "hs"}:
        mult = AZSQL_HS_MULTIPLIER

    compute_hourly = float(vcores) * base * mult
    if lic == "AHUB":
        compute_hourly *= (1.0 - AZSQL_AHUB_DISCOUNT)

    monthly = compute_hourly * float(hours) + max(0.0, float(storage_gb)) * AZSQL_STORAGE_GB_MONTH
    return round(monthly, 2)

def _load_override_json(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# ---------- Azure pricing ----------
# Optional override file: ./prices/azure_compute_prices.json
# [{"region":"eastus","sku":"Standard_D4s_v5","os":"linux","license_model":"BYOL","hourly":0.20}, ...]
# ----- Azure Retail Prices API (live) with local cache -----
def _azure_cache_path(region: str) -> Path:
    Path("prices").mkdir(exist_ok=True, parents=True)
    return Path(f"prices/azure_compute_cache_{region}.json")

def _normalize_azure_sku(sku: str) -> str:
    # "Standard_D4s_v5" -> "D4s v5"; "Standard_F8s_v2" -> "F8s v2"
    s = str(sku or "")
    if s.startswith("Standard_"):
        s = s[len("Standard_"):]
    return s.replace("_", " ")

def _azure_cache_load(region: str) -> Dict[str, dict]:
    p = _azure_cache_path(region)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _azure_cache_save(region: str, data: Dict[str, dict]) -> None:
    try:
        with open(_azure_cache_path(region), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def _azure_price_key(sku_core: str, os_name: str) -> str:
    # cache key, e.g., "D4s v5|linux"
    return f"{sku_core.strip()}|{os_name.strip().lower()}"

def _azure_fetch_retail_prices(region: str, sku_core: str, os_name: str, timeout_s: float = 15.0) -> Optional[float]:
    """
    Call Azure Retail Prices API to get On-Demand hourly price for a VM SKU in a given region.
    """
    base = "https://prices.azure.com/api/retail/prices"
    arm = region.replace("'", "''")
    sku = sku_core.replace("'", "''")
    filt = (
        f"serviceName eq 'Virtual Machines' and "
        f"armRegionName eq '{arm}' and "
        f"skuName eq '{sku}' and "
        f"priceType eq 'Consumption'"
    )
    url = f"{base}?$filter={requests.utils.quote(filt, safe=' =\'')}"
    best_linux = None
    best_windows = None
    while url:
        resp = requests.get(url, timeout=timeout_s)
        if resp.status_code != 200:
            break
        data = resp.json()
        items = data.get("Items", []) or data.get("items", [])
        for it in items:
            if str(it.get("type","")).lower() != "consumption":
                continue
            if "spot" in str(it.get("meterName","")).lower() or "low priority" in str(it.get("meterName","")).lower():
                continue
            if it.get("unitOfMeasure") not in ("1 Hour", "Hour"):
                continue
            price = it.get("retailPrice")
            currency = it.get("currencyCode", "USD")
            if price is None or currency != "USD":
                continue
            pname = str(it.get("productName","")).lower()
            mname = str(it.get("meterName","")).lower()
            is_windows = ("windows" in pname) or ("windows" in mname)
            is_linux = ("linux" in pname) or ("linux" in mname) or (not is_windows)
            if is_windows:
                best_windows = float(price) if best_windows is None else min(best_windows, float(price))
            elif is_linux:
                best_linux = float(price) if best_linux is None else min(best_linux, float(price))
        url = data.get("NextPageLink") or data.get("nextPageLink")
    if os_name.strip().lower() == "windows":
        return best_windows if best_windows is not None else best_linux
    else:
        return best_linux if best_linux is not None else best_windows

def _azure_live_compute_hourly(region: str, sku: str, os_name: str, refresh: bool = False, ttl_days: float | None = None) -> Optional[float]:
    region = region.strip().lower()
    sku_core = _normalize_azure_sku(sku)
    key = _azure_price_key(sku_core, os_name)
    cache_path = _azure_cache_path(region)
    cache_ok = (not refresh) and (ttl_days is None or _cache_age_days(cache_path) <= float(ttl_days))
    cache = _azure_cache_load(region) if cache_ok else {}
    hit = cache.get(key)
    if hit and cache_ok:
        val = hit.get("price")
        if isinstance(val, (int, float)):
            return float(val)
    price = _azure_fetch_retail_prices(region, sku_core, os_name)
    if price is None:
        return None
    cache[key] = {"price": float(price), "ts": int(time.time())}
    _azure_cache_save(region, cache)
    return float(price)

def _azure_price_override(region: str, sku: str, os_name: str, license_model: str) -> Optional[float]:
    p = Path("prices/azure_compute_prices.json")
    if not p.exists(): return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            rows = json.load(f)
        for r in rows:
            if (r.get("region")==region and r.get("sku")==sku
                and r.get("os","linux").lower()==os_name.lower()
                and r.get("license_model","BYOL").upper()==license_model.upper()):
                return float(r["hourly"])
    except Exception:
        return None
    return None

_AZ_BASE_DEFAULT_HOURLY = float(os.getenv("AZURE_BASE_DEFAULT_HOURLY", "0.20"))
_AZ_OS_UPLIFT = {
    "linux": 0.00,
    "windows": 0.12,
    "rhel": 0.09,
    "suse": 0.07,
}

def azure_vm_price_hourly(region: str, sku: str, os_name: str, license_model: str, refresh=False, ttl_days: float | None = None) -> float:
    live = _azure_live_compute_hourly(region, sku, os_name, refresh=refresh, ttl_days=ttl_days)
    if live is not None:
        return live
    # 1) User override JSON
    o = _azure_price_override(region, sku, os_name, license_model)
    if o is not None:
        return o
    # 2) Heuristic fallback (base + OS uplift)
    uplift = _AZ_OS_UPLIFT.get(os_name.strip().lower(), 0.0)
    if str(license_model).strip().lower() != "byol":
        uplift += 0.01
    return _AZ_BASE_DEFAULT_HOURLY + uplift

# ---------- Monthly calculators ----------
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

