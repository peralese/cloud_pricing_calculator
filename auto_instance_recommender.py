import argparse
import csv
import json
import sys
import os
import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from glob import glob
import math
from decimal import Decimal

# ---------- Cost model defaults (override via env if desired) ----------
# S3 Standard storage ($/GB-month)
S3_STD_GB_MONTH = float(os.getenv("S3_STD_GB_MONTH", "0.023"))

# EBS gp3 storage ($/GB-month). (Simplified: size-only; you can later add IOPS/throughput add-ons)
EBS_GP3_GB_MONTH = float(os.getenv("EBS_GP3_GB_MONTH", "0.08"))
# EBS io1 storage ($/GB-month)  (optional alternative)
EBS_IO1_GB_MONTH = float(os.getenv("EBS_IO1_GB_MONTH", "0.125"))

# Data transfer out to internet baseline ($/GB) – simplified
DTO_GB_PRICE = float(os.getenv("DTO_GB_PRICE", "0.09"))

# Network profile → assumed egress GB/month (tune to your environment)
NETWORK_PROFILE_TO_GB = {
    "low":   float(os.getenv("NETWORK_EGRESS_GB_LOW", "50")),
    "medium":float(os.getenv("NETWORK_EGRESS_GB_MED", "500")),
    "high":  float(os.getenv("NETWORK_EGRESS_GB_HIGH", "5000")),
}

def _find_latest_output(patterns=("output/recommend_*.csv", "output/recommend_*.xlsx")) -> Optional[Path]:
    """
    Return the newest file matching the given glob patterns, or None if not found.
    """
    candidates = []
    for pat in patterns:
        candidates.extend(glob(pat))
    if not candidates:
        return None
    newest = max((Path(p) for p in candidates), key=lambda p: p.stat().st_mtime)
    return newest
# ---------- Output helpers ----------
def make_output_path(cmd: str, user_out: Optional[str] = None) -> str:
    """
    Build the output file path.
    - If user provided --out, honor it.
    - Otherwise, place file in ./output/<cmd>_<timestamp>.csv
    """
    if user_out:
        return user_out
    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return str(out_dir / f"{cmd}_{ts}.csv")

# Load environment variables from .env file if present (optional)
try:
    from dotenv import load_dotenv  # pip install python-dotenv
    load_dotenv()
except Exception:
    pass

def _as_float(x, default=0.0):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default

def _as_int(x, default=0):
    try:
        if x is None or x == "":
            return default
        return int(float(x))
    except Exception:
        return default

def _as_bool(x, default=False):
    if isinstance(x, bool):
        return x
    if x is None:
        return default
    s = str(x).strip().lower()
    if s in {"y","yes","true","1"}:
        return True
    if s in {"n","no","false","0"}:
        return False
    return default

# ---------- Lazy imports ----------
def _lazy_boto3():
    try:
        import boto3  # type: ignore
        return boto3
    except ImportError:
        print("This script requires boto3. Install with: pip install boto3", file=sys.stderr)
        sys.exit(1)

def _lazy_pandas():
    try:
        import pandas as pd  # type: ignore
        return pd
    except ImportError:
        print("Excel input requested but pandas is missing. Install with:\n  pip install pandas openpyxl", file=sys.stderr)
        sys.exit(1)

# ---------- Family sets & simple heuristics ----------
FAMILY_PREFS = {
    "balanced": ["m7i", "m6i", "m5"],
    "compute":  ["c7i", "c6i", "c5"],
    "memory":   ["r7i", "r6i", "r5"],
}

def infer_profile(vcpu: int, mem_gib: float) -> str:
    """Basic heuristic from memory-per-vCPU."""
    if vcpu <= 0 or mem_gib <= 0:
        return "balanced"
    mem_per_vcpu = mem_gib / vcpu
    if mem_per_vcpu <= 3.0:
        return "compute"
    if mem_per_vcpu >= 6.0:
        return "memory"
    return "balanced"

# ---------- EC2 Catalog ----------
def fetch_instance_catalog(region: str) -> Dict[str, dict]:
    """
    Return a dict {instanceType -> details} for current-gen x86_64, non-metal in the given region.
    """
    boto3 = _lazy_boto3()
    ec2 = boto3.client("ec2", region_name=region)
    paginator = ec2.get_paginator("describe_instance_types")
    page_it = paginator.paginate(Filters=[{"Name": "current-generation", "Values": ["true"]}])

    catalog = {}
    for page in page_it:
        for it in page.get("InstanceTypes", []):
            itype = it["InstanceType"]
            if itype.endswith(".metal"):
                continue
            archs = it.get("ProcessorInfo", {}).get("SupportedArchitectures", [])
            if "x86_64" not in archs:
                continue
            vcpu = it.get("VCpuInfo", {}).get("DefaultVCpus", 0)
            mem_mib = it.get("MemoryInfo", {}).get("SizeInMiB", 0)
            if vcpu <= 0 or mem_mib <= 0:
                continue
            catalog[itype] = {
                "instanceType": itype,
                "vcpu": vcpu,
                "memory_gib": mem_mib / 1024.0,
            }
    return catalog

def _family_rank(families: List[str], itype: str) -> int:
    fam = itype.split(".")[0]
    try:
        return families.index(fam)
    except ValueError:
        return len(families) + 1

def pick_instance(catalog: Dict[str, dict], profile: str, need_vcpu: int, need_mem_gib: float) -> Optional[dict]:
    """
    Choose the smallest instance in preferred families that satisfies requirements.
    Falls back to any current-gen x86 if preferred families don't fit.
    """
    families = FAMILY_PREFS.get(profile, FAMILY_PREFS["balanced"])
    candidates = [t for t, info in catalog.items() if info["vcpu"] >= need_vcpu and info["memory_gib"] >= need_mem_gib]
    if not candidates:
        return None
    def sort_key(itype: str) -> Tuple[int, int, float, str]:
        info = catalog[itype]
        return (_family_rank(families, itype), info["vcpu"], info["memory_gib"], itype)
    candidates.sort(key=sort_key)
    return catalog[candidates[0]]

# --- Diagnostics helpers (for fit transparency) ---
def _smallest_meeting_cpu(catalog: Dict[str, dict], vcpu_needed: int) -> Optional[dict]:
    fits = [info for info in catalog.values() if info["vcpu"] >= vcpu_needed]
    if not fits:
        return None
    fits.sort(key=lambda x: (x["vcpu"], x["memory_gib"], x["instanceType"]))
    return fits[0]

def _smallest_meeting_mem(catalog: Dict[str, dict], mem_needed: float) -> Optional[dict]:
    fits = [info for info in catalog.values() if info["memory_gib"] >= mem_needed]
    if not fits:
        return None
    fits.sort(key=lambda x: (x["memory_gib"], x["vcpu"], x["instanceType"]))
    return fits[0]

# ---------- Pricing ----------
AWS_REGION_TO_LOCATION = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "ca-central-1": "Canada (Central)",
    "eu-central-1": "EU (Frankfurt)",
    "eu-central-2": "EU (Zurich)",
    "eu-west-1": "EU (Ireland)",
    "eu-west-2": "EU (London)",
    "eu-west-3": "EU (Paris)",
    "eu-north-1": "EU (Stockholm)",
    "eu-south-1": "EU (Milan)",
    "eu-south-2": "EU (Spain)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "ap-south-2": "Asia Pacific (Hyderabad)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-northeast-3": "Asia Pacific (Osaka)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-southeast-3": "Asia Pacific (Jakarta)",
    "ap-southeast-4": "Asia Pacific (Melbourne)",
    "ap-east-1": "Asia Pacific (Hong Kong)",
    "sa-east-1": "South America (Sao Paulo)",
    "me-south-1": "Middle East (Bahrain)",
    "me-central-1": "Middle East (UAE)",
    "af-south-1": "Africa (Cape Town)",
}

def price_ec2_ondemand(instance_type: str, region: str, os_name: str = "Linux") -> Optional[float]:
    """Returns hourly USD On-Demand price for the given instance type/region/OS."""
    boto3 = _lazy_boto3()
    location = AWS_REGION_TO_LOCATION.get(region)
    if not location:
        return None
    pricing = boto3.client("pricing", region_name="us-east-1")  # Pricing API endpoint
    filters = [
        {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
        {"Type": "TERM_MATCH", "Field": "location", "Value": location},
        {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": os_name},
        {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
        {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
        {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
    ]
    resp = pricing.get_products(ServiceCode="AmazonEC2", Filters=filters, MaxResults=100)
    for pl in resp.get("PriceList", []):
        o = json.loads(pl)
        for term in o.get("terms", {}).get("OnDemand", {}).values():
            for dim in term.get("priceDimensions", {}).values():
                if dim.get("unit") == "Hrs":
                    usd = dim.get("pricePerUnit", {}).get("USD")
                    if usd is not None:
                        try:
                            return float(usd)
                        except ValueError:
                            pass
    return None
def _pricing_first_usd(pl_obj: dict) -> Optional[float]:
    # Extract first USD "Hrs" or "Quantity" price from a PriceList product
    for term in pl_obj.get("terms", {}).get("OnDemand", {}).values():
        for dim in term.get("priceDimensions", {}).values():
            usd = dim.get("pricePerUnit", {}).get("USD")
            unit = dim.get("unit")
            if usd and unit in {"Hrs", "Quantity"}:
                try:
                    return float(usd)
                except Exception:
                    pass
    return None

def price_rds_ondemand(engine: str, instance_class: str, region: str, license_model: str = "AWS", multi_az: bool = False) -> Optional[float]:
    """
    Returns hourly USD On-Demand for an RDS instance.
    engine: 'Postgres', 'MySQL', 'SQLServer', etc.
    instance_class: e.g., 'db.m5.large'
    license_model: 'AWS' (license-included) or 'BYOL'
    multi_az: True/False
    """
    boto3 = _lazy_boto3()
    location = AWS_REGION_TO_LOCATION.get(region)
    if not location:
        return None

    # Map our license_model to RDS "licenseModel" field
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
        if usd is not None:
            return usd
    return None

# ---------- I/O (CSV + Excel) ----------
def _prompt_for_input_path() -> Path:
    print("Enter the path to your input file (.csv, .xlsx, or .xls):")
    while True:
        raw = input("> ").strip().strip('"').strip("'")
        if not raw:
            print("Please provide a file path.")
            continue
        p = Path(raw).expanduser()
        if not p.exists():
            print(f"❌ File not found: {p}\nTry again:")
            continue
        if p.suffix.lower() not in {".csv", ".xlsx", ".xls"}:
            print("❌ Unsupported file type. Please provide .csv, .xlsx, or .xls")
            continue
        return p

def _maybe_prompt_for_sheet(path: Path, sheet: Optional[str]) -> Optional[str]:
    if path.suffix.lower() in {".xlsx", ".xls"} and not sheet:
        print("Excel file detected. Enter a sheet name (or press Enter for the first sheet):")
        s = input("> ").strip()
        return s or None
    return sheet

def read_rows(path: str, sheet: Optional[str] = None) -> List[dict]:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".csv":
        with open(p, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    elif suffix in {".xlsx", ".xls"}:
        pd = _lazy_pandas()
        try:
            df = pd.read_excel(p, sheet_name=sheet if sheet is not None else 0)
        except Exception as e:
            print(f"❌ Failed to read Excel file: {e}", file=sys.stderr)
            sys.exit(1)
        df.columns = [str(c).strip() for c in df.columns]
        return df.to_dict(orient="records")
    else:
        print("❌ Unsupported input file format (use .csv, .xlsx, or .xls)", file=sys.stderr)
        sys.exit(1)

def write_rows(path: str, rows: List[dict], fieldnames: List[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

# ---------- CLI Commands ----------
def cmd_recommend(args):
    # Resolve region: CLI arg -> AWS_REGION env -> profile default
    region = args.region or os.getenv("AWS_REGION")
    if not region:
        boto3 = _lazy_boto3()
        region = boto3.session.Session().region_name
    if not region:
        raise SystemExit("Region not provided. Pass --region or set AWS_REGION / profile region.")

    # Input path (prompt if omitted) and Excel sheet handling
    if not args.input:
        ipath = _prompt_for_input_path()
        args.sheet = _maybe_prompt_for_sheet(ipath, args.sheet)
        args.input = str(ipath)
    else:
        given = Path(args.input)
        args.sheet = _maybe_prompt_for_sheet(given, args.sheet)

    catalog = fetch_instance_catalog(region)
    rows = read_rows(args.input, sheet=args.sheet)
    if not rows:
        raise SystemExit("❌ Input file has no rows.")

    out_rows = []
    for r in rows:
        try:
            rid = r["id"]
            vcpu = int(r["vcpu"])
            mem_gib = float(r["memory_gib"])
        except Exception:
            print(f"Skipping row with missing/invalid values: {r}", file=sys.stderr)
            continue

        prof = (str(r.get("profile", "")).strip().lower() if r.get("profile") is not None else "")
        if prof not in ("balanced", "compute", "memory"):
            prof = infer_profile(vcpu, mem_gib)

        chosen = pick_instance(catalog, prof, vcpu, mem_gib)

        overprov_vcpu, overprov_mem_gib, fit_reason = "", "", ""
        if chosen:
            overprov_vcpu = chosen["vcpu"] - vcpu
            overprov_mem_gib = round(chosen["memory_gib"] - mem_gib, 2)
            if chosen["vcpu"] == vcpu and round(chosen["memory_gib"], 2) == round(mem_gib, 2):
                fit_reason = "exact"
            else:
                cpu_only = _smallest_meeting_cpu(catalog, vcpu)
                mem_only = _smallest_meeting_mem(catalog, mem_gib)
                def rank(x): return (x["vcpu"], x["memory_gib"]) if x else (float("inf"), float("inf"))
                if cpu_only or mem_only:
                    fit_reason = "memory-bound" if rank(mem_only) >= rank(cpu_only) else "cpu-bound"
                else:
                    fit_reason = "no-fit-fallback"

        base = dict(r)  # carry ALL original columns through

        base.update({
            "id": rid,
            "requested_vcpu": vcpu,
            "requested_memory_gib": mem_gib,
            "profile": prof,
            "region": region,
            "recommended_instance_type": chosen["instanceType"] if chosen else "",
            "rec_vcpu": chosen["vcpu"] if chosen else "",
            "rec_memory_gib": f'{chosen["memory_gib"]:.2f}' if chosen else "",
            "overprov_vcpu": overprov_vcpu,
            "overprov_mem_gib": overprov_mem_gib,
            "fit_reason": fit_reason,
            "note": "" if chosen else "No matching current-gen x86_64 found; consider GPU/ARM or older-gen.",
        })
        out_rows.append(base)
    
    out_path = make_output_path("recommend", args.output)

    preferred = [
        "id","requested_vcpu","requested_memory_gib","profile","region",
        "recommended_instance_type","rec_vcpu","rec_memory_gib",
        "overprov_vcpu","overprov_mem_gib","fit_reason","note"
    ]

    # Collect every key that appears in any row
    all_keys = {k for row in out_rows for k in row.keys()}

    # Start with preferred order, then append anything else
    fieldnames = preferred + [k for k in all_keys if k not in preferred]

    write_rows(out_path, out_rows, fieldnames)
    print(f"Wrote recommendations → {out_path}")


def cmd_price(args):
    # 1) If no --in was provided, try to auto-pick the newest recommendation output.
        # Handle --latest flag explicitly
    if getattr(args, "latest", False):
        latest = _find_latest_output()
        if not latest:
            raise SystemExit("❌ --latest was set but no recommendation files found in ./output")
        print(f"ℹ️  Using latest recommendation file (via --latest): {latest}")
        args.input = str(latest)
        args.sheet = _maybe_prompt_for_sheet(latest, args.sheet)

    if not args.input:
        latest = _find_latest_output()
        if latest:
            print(f"ℹ️  No --in provided. Using latest recommendation file: {latest}")
            args.input = str(latest)
            # Excel sheet prompt only applies if we picked an .xlsx file
            args.sheet = _maybe_prompt_for_sheet(latest, args.sheet)
        else:
            # 2) Fall back to interactive prompt (CSV or Excel) if nothing in output/.
            ipath = _prompt_for_input_path()
            args.sheet = _maybe_prompt_for_sheet(ipath, args.sheet)
            args.input = str(ipath)
    else:
        given = Path(args.input)
        args.sheet = _maybe_prompt_for_sheet(given, args.sheet)

    # Read input (CSV/Excel)
    rows = read_rows(args.input, sheet=args.sheet)
    if not rows:
        raise SystemExit("❌ Input file has no rows.")
    # Price rows
    out_rows = []
    for r in rows:
        # --------- Inputs from row (with safe defaults) ----------
        itype = r.get("recommended_instance_type") or r.get("instance_type") or ""
        region_row = r.get("region") or args.region
        os_row = (r.get("os") or args.os or "Linux").strip()
        license_model = (r.get("license_model") or "AWS").strip()  # 'AWS' or 'BYOL'
        ebs_gb = _as_float(r.get("ebs_gb"), 0.0)
        ebs_type = (r.get("ebs_type") or "gp3").strip()
        ebs_iops = _as_int(r.get("ebs_iops"), 0)  # currently unused in simplified model
        s3_gb = _as_float(r.get("s3_gb"), 0.0)
        net_prof = (r.get("network_profile") or "").strip()
        db_engine = (r.get("db_engine") or "").strip()
        db_class = (r.get("db_instance_class") or "").strip()
        db_storage_gb = _as_float(r.get("db_storage_gb"), 0.0)  # not charged separately here; could be added later via pricing API
        db_multi_az = _as_bool(r.get("multi_az"), False)

        # --------- Compute OS to use for EC2 compute price ----------
        # If BYOL → charge compute at Linux rate (no OS uplift). Else use declared OS.
        os_for_compute = "Linux" if license_model.lower() == "byol" else os_row

        # --------- EC2 hourly price ----------
        if not itype or not region_row:
            # Missing required fields for compute pricing
            compute_price = None
            r["pricing_note"] = "Missing instance_type or region"
        else:
            compute_price = price_ec2_ondemand(itype, region_row, os_name=os_for_compute)
            if compute_price is None:
                r["pricing_note"] = "No EC2 price found (check filters/region/OS)"
            else:
                r["pricing_note"] = r.get("pricing_note","")

        # --------- Monthly compute ----------
        hours = float(args.hours_per_month)
        compute_monthly = monthly_compute_cost(compute_price, hours)

        if getattr(args, "no_monthly", False):
            r["price_per_hour_usd"] = f"{compute_price:.6f}" if compute_price is not None else ""
            # blank all monthly columns when --no-monthly is set
            r["monthly_compute_usd"] = ""
            r["monthly_ebs_usd"] = ""
            r["monthly_s3_usd"] = ""
            r["monthly_network_usd"] = ""
            r["monthly_db_usd"] = ""
            r["monthly_total_usd"] = ""
        else:
            r["price_per_hour_usd"] = f"{compute_price:.6f}" if compute_price is not None else ""
            r["monthly_compute_usd"] = f"{compute_monthly:.2f}"
            r["monthly_ebs_usd"] = f"{monthly_ebs_cost(ebs_gb, ebs_type):.2f}"
            r["monthly_s3_usd"] = f"{monthly_s3_cost(s3_gb):.2f}"
            r["monthly_network_usd"] = f"{monthly_network_cost(net_prof):.2f}"
            if db_engine and db_class and region_row:
                db_monthly = monthly_rds_cost(db_engine, db_class, region_row, license_model, db_multi_az, hours)
            else:
                db_monthly = 0.0
            r["monthly_db_usd"] = f"{db_monthly:.2f}"
            parts = [
                _as_float(r["monthly_compute_usd"], 0.0),
                _as_float(r["monthly_ebs_usd"], 0.0),
                _as_float(r["monthly_s3_usd"], 0.0),
                _as_float(r["monthly_network_usd"], 0.0),
                _as_float(r["monthly_db_usd"], 0.0),
            ]
            r["monthly_total_usd"] = f"{sum(parts):.2f}"
                    
        out_rows.append(r)

    # Preserve columns + add pricing fields if absent
    fieldnames = list(out_rows[0].keys()) if out_rows else []
    for col in [
        "price_per_hour_usd",
        "monthly_compute_usd",
        "monthly_ebs_usd",
        "monthly_s3_usd",
        "monthly_network_usd",
        "monthly_db_usd",
        "monthly_total_usd",
        "pricing_note",
    ]:
        if col not in fieldnames:
            fieldnames.append(col)

    out_path = make_output_path("price", args.output)
    print(f"Input:  {args.input}")
    print(f"Output: {out_path}")
    write_rows(out_path, out_rows, fieldnames)
    print(f"Wrote priced recommendations → {out_path}")

def monthly_compute_cost(price_per_hour: Optional[float], hours: float) -> float:
    return round((price_per_hour or 0.0) * hours, 2)

def monthly_ebs_cost(ebs_gb: float, ebs_type: str = "gp3") -> float:
    et = (ebs_type or "gp3").strip().lower()
    if et == "io1":
        rate = EBS_IO1_GB_MONTH
    else:
        rate = EBS_GP3_GB_MONTH
    return round(max(0.0, ebs_gb) * rate, 2)

def monthly_s3_cost(s3_gb: float) -> float:
    return round(max(0.0, s3_gb) * S3_STD_GB_MONTH, 2)

def monthly_network_cost(profile: str) -> float:
    if not profile:
        return 0.0
    gb = NETWORK_PROFILE_TO_GB.get(profile.strip().lower())
    if gb is None:
        return 0.0
    return round(gb * DTO_GB_PRICE, 2)

def monthly_rds_cost(engine: str, instance_class: str, region: str, license_model: str, multi_az: bool, hours: float) -> float:
    p = price_rds_ondemand(engine, instance_class, region, license_model=license_model, multi_az=multi_az)
    return round((p or 0.0) * hours, 2)


def main():
    p = argparse.ArgumentParser(description="Recommend AWS EC2 types from CPU/RAM, then price them. (CSV or Excel input)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("recommend", help="Recommend EC2 instance types")
    p1.add_argument("--region", required=False, help="AWS region, e.g., us-east-1")
    p1.add_argument("--in", dest="input", required=False, help="Input CSV/Excel (id,vcpu,memory_gib[,profile])")
    p1.add_argument("--sheet", required=False, help="Excel sheet name (if input is .xlsx/.xls)")
    p1.add_argument("--out", dest="output", required=False, help="Output CSV (default: ./output/recommend_<timestamp>.csv)")
    p1.set_defaults(func=cmd_recommend)

    p2 = sub.add_parser("price", help="Add On-Demand pricing to a recommendation CSV/Excel")
    p2.add_argument("--region", help="AWS region (optional if present per-row)")
    p2.add_argument("--os", choices=["Linux","Windows"], default="Linux")
    p2.add_argument("--in", dest="input", required=False, help="Input CSV/Excel (from recommend step)")
    p2.add_argument("--sheet", required=False, help="Excel sheet name (if input is .xlsx/.xls)")
    p2.add_argument("--out", dest="output", required=False, help="Output CSV (default: ./output/price_<timestamp>.csv)")
    p2.add_argument("--latest", action="store_true",help="Use newest output/recommend_* and fail if none found")
    p2.add_argument("--hours-per-month", type=float, default=730.0, help="Hours used to compute monthly cost (default: 730)")
    p2.add_argument("--no-monthly", action="store_true", help="Do not compute monthly cost column")
    p2.set_defaults(func=cmd_price)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()

