import argparse
import csv
import json
import sys
import os
import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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

        out_rows.append({
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

    fieldnames = [
        "id","requested_vcpu","requested_memory_gib","profile","region",
        "recommended_instance_type","rec_vcpu","rec_memory_gib",
        "overprov_vcpu","overprov_mem_gib","fit_reason","note"
    ]
    out_path = make_output_path("recommend", args.output)
    write_rows(out_path, out_rows, fieldnames)
    print(f"Wrote recommendations → {out_path}")

def cmd_price(args):
    # Input path (prompt if omitted) and Excel sheet handling
    if not args.input:
        ipath = _prompt_for_input_path()
        args.sheet = _maybe_prompt_for_sheet(ipath, args.sheet)
        args.input = str(ipath)
    else:
        given = Path(args.input)
        args.sheet = _maybe_prompt_for_sheet(given, args.sheet)

    rows = read_rows(args.input, sheet=args.sheet)

    out_rows = []
    for r in rows:
        itype = r.get("recommended_instance_type") or r.get("instance_type") or ""
        region = r.get("region") or args.region
        if not itype or not region:
            r["price_per_hour_usd"] = ""
            r["pricing_note"] = "Missing instance_type or region"
            out_rows.append(r)
            continue
        price = price_ec2_ondemand(itype, region, os_name=args.os)
        r["price_per_hour_usd"] = f"{price:.6f}" if price is not None else ""
        r["pricing_note"] = "" if price is not None else "No price found (check filters/region/OS)"
        out_rows.append(r)

    # Preserve columns + add pricing fields if absent
    fieldnames = list(out_rows[0].keys()) if out_rows else []
    if "price_per_hour_usd" not in fieldnames: fieldnames.append("price_per_hour_usd")
    if "pricing_note" not in fieldnames: fieldnames.append("pricing_note")

    out_path = make_output_path("price", args.output)
    write_rows(out_path, out_rows, fieldnames)
    print(f"Wrote priced recommendations → {out_path}")

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
    p2.set_defaults(func=cmd_price)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()

