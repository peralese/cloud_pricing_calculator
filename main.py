# main.py
import argparse, os, sys, datetime, json
from pathlib import Path
from typing import Optional, List, Dict
from recommender import (
    infer_profile,
    fetch_instance_catalog,           # AWS
    pick_instance,
    smallest_meeting_cpu,
    smallest_meeting_mem,
    fetch_azure_vm_catalog,           # Azure
    pick_azure_size,
)
from pricing import (
    read_rows, write_rows,
    price_ec2_ondemand, price_rds_ondemand,
    monthly_compute_cost, monthly_ebs_cost, monthly_s3_cost,
    monthly_network_cost, monthly_rds_cost,
)

from recommender import (
    infer_profile,
    fetch_instance_catalog, pick_instance, smallest_meeting_cpu, smallest_meeting_mem,
    fetch_azure_vm_catalog, pick_azure_size,
    normalize_azure_region,  # <-- add this
)

# ---------- Simple I/O helpers ----------
def find_latest_output(patterns=("output/recommend_*.csv","output/recommend_*.xlsx")) -> Optional[Path]:
    from glob import glob
    candidates: List[str] = []
    for pat in patterns:
        candidates.extend(glob(pat))
    if not candidates:
        return None
    return max((Path(p) for p in candidates), key=lambda p: p.stat().st_mtime)

def make_output_path(cmd: str, user_out: Optional[str] = None) -> str:
    if user_out:
        return user_out
    out_dir = Path("output"); out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return str(out_dir / f"{cmd}_{ts}.csv")

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

# ---------- Small parsers ----------
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

# ---------- Cloud helpers ----------
def cloud_from_str(s: Optional[str]) -> str:
    s = (s or "").strip().lower()
    if s in ("azure","az"): return "azure"
    return "aws"

def cloud_for_row(row: dict, default_cloud: str) -> str:
    return cloud_from_str(row.get("cloud")) if row.get("cloud") else default_cloud

# ---------- Commands ----------
def cmd_recommend(args):
    # default_cloud = cloud_from_str(getattr(args, "cloud", "aws"))
    default_cloud = cloud_from_str(args.cloud)

    # Region handling (AWS only; Azure will use eastus if row has no region)
    region_aws = args.region or os.getenv("AWS_REGION")
    if default_cloud == "aws" and not region_aws:
        try:
            import boto3
            region_aws = boto3.session.Session().region_name
        except Exception:
            pass
    if default_cloud == "aws" and not region_aws:
        raise SystemExit("AWS region not provided. Pass --region or set AWS_REGION / profile region.")

    # Input selection
    if not args.input:
        ipath = prompt_for_input_path()
        args.sheet = maybe_prompt_for_sheet(ipath, args.sheet)
        args.input = str(ipath)
    else:
        args.sheet = maybe_prompt_for_sheet(Path(args.input), args.sheet)

    rows = read_rows(args.input, sheet=args.sheet)
    if not rows:
        raise SystemExit("❌ Input file has no rows.")

    aws_catalog = None  # lazy load if any row uses AWS
    azure_catalog_by_region: Dict[str, Dict[str,dict]] = {}

    out_rows: List[dict] = []
    for r in rows:
        try:
            rid = r["id"]; vcpu = int(r["vcpu"]); mem_gib = float(r["memory_gib"])
        except Exception:
            print(f"Skipping row with missing/invalid values: {r}", file=sys.stderr)
            continue

        prof = (str(r.get("profile","")).strip().lower() if r.get("profile") is not None else "")
        if prof not in ("balanced","compute","memory"):
            prof = infer_profile(vcpu, mem_gib)

        # row_cloud = cloud_for_row(r, default_cloud)
        row_cloud = default_cloud  # enforce single-cloud run; ignore any input 'cloud' column

        # Select region per cloud
        if row_cloud == "azure":
            from recommender import normalize_azure_region  # add at top with other imports
            az_region = normalize_azure_region(r.get("region") or "eastus")
            if az_region not in azure_catalog_by_region:
                azure_catalog_by_region[az_region] = fetch_azure_vm_catalog(az_region)
            chosen = pick_azure_size(azure_catalog_by_region[az_region], vcpu, mem_gib)
            out_region = az_region
        else:
            if aws_catalog is None:
                if not region_aws:
                    raise SystemExit("AWS region required for AWS recommendations.")
                aws_catalog = fetch_instance_catalog(region_aws)
            chosen = pick_instance(aws_catalog, prof, vcpu, mem_gib)
            out_region = region_aws

        overprov_vcpu = overprov_mem_gib = fit_reason = ""
        if chosen:
            overprov_vcpu = chosen["vcpu"] - vcpu
            overprov_mem_gib = round(chosen["memory_gib"] - mem_gib, 2)
            if chosen["vcpu"] == vcpu and round(chosen["memory_gib"],2) == round(mem_gib,2):
                fit_reason = "exact"
            elif row_cloud == "aws":
                cpu_only = smallest_meeting_cpu(aws_catalog, vcpu) if aws_catalog else None
                mem_only = smallest_meeting_mem(aws_catalog, mem_gib) if aws_catalog else None
                def rank(x): return (x["vcpu"], x["memory_gib"]) if x else (float("inf"), float("inf"))
                if cpu_only or mem_only:
                    fit_reason = "memory-bound" if rank(mem_only) >= rank(cpu_only) else "cpu-bound"
                else:
                    fit_reason = "no-fit-fallback"

        base = dict(r)
        base.update({
            "id": rid,
            # "cloud": row_cloud,
            "cloud": row_cloud,  # stamped from CLI, not from input
            "requested_vcpu": vcpu,
            "requested_memory_gib": mem_gib,
            "profile": prof,
            "region": out_region,
            "recommended_instance_type": chosen["instanceType"] if chosen else "",
            "rec_vcpu": chosen["vcpu"] if chosen else "",
            "rec_memory_gib": f'{chosen["memory_gib"]:.2f}' if chosen else "",
            "overprov_vcpu": overprov_vcpu,
            "overprov_mem_gib": overprov_mem_gib,
            "fit_reason": fit_reason,
            "note": "" if chosen else ("No matching size found in region." if row_cloud=="azure"
                                       else "No matching current-gen x86_64 found; consider GPU/ARM or older-gen."),
        })
        out_rows.append(base)

    out_path = make_output_path("recommend", args.output)
    preferred = [
        "id","cloud","requested_vcpu","requested_memory_gib","profile","region",
        "recommended_instance_type","rec_vcpu","rec_memory_gib",
        "overprov_vcpu","overprov_mem_gib","fit_reason","note"
    ]
    all_keys = {k for row in out_rows for k in row.keys()}
    fieldnames = preferred + [k for k in all_keys if k not in preferred]
    write_rows(out_path, out_rows, fieldnames)
    print(f"Wrote recommendations → {out_path}")

def cmd_price(args):
    expected_cloud = cloud_from_str(args.cloud)

    # Choose input file (respect --latest)
    if getattr(args, "latest", False):
        latest = find_latest_output()
        if not latest:
            raise SystemExit("❌ --latest was set but no recommendation files found in ./output")
        print(f"ℹ️  Using latest recommendation file (via --latest): {latest}")
        args.input = str(latest)
        args.sheet = maybe_prompt_for_sheet(latest, args.sheet)

    if not args.input:
        latest = find_latest_output()
        if latest:
            print(f"ℹ️  No --in provided. Using latest recommendation file: {latest}")
            args.input = str(latest)
            args.sheet = maybe_prompt_for_sheet(latest, args.sheet)
        else:
            ipath = prompt_for_input_path()
            args.sheet = maybe_prompt_for_sheet(ipath, args.sheet)
            args.input = str(ipath)
    else:
        args.sheet = maybe_prompt_for_sheet(Path(args.input), args.sheet)

    # ---- READ ROWS FIRST ----
    rows = read_rows(args.input, sheet=args.sheet)
    if not rows:
        raise SystemExit("❌ Input file has no rows.")

    # ---- VALIDATE SINGLE-CLOUD FILE (must match CLI) ----
    seen = {(str(r.get("cloud", "")).strip().lower() or "") for r in rows}
    seen.discard("")  # allow older files with no 'cloud'; they'll inherit CLI cloud
    if seen:
        if len(seen) > 1 or next(iter(seen)) != expected_cloud:
            human = ", ".join(sorted(seen))
            raise SystemExit(
                f"❌ This price run is for '--cloud {expected_cloud}', but the file contains cloud={human}. "
                "Please price with the matching --cloud or re-run 'recommend' for a single cloud."
            )

    out_rows = []
    for r in rows:
        # enforce CLI cloud for this run
        row_cloud = expected_cloud
        r["cloud"] = expected_cloud  # ensure output is explicit

        itype = r.get("recommended_instance_type") or r.get("instance_type") or ""
        # Region default per cloud
        region_row = (r.get("region") or args.region or ("eastus" if row_cloud == "azure" else None))
        os_row = (r.get("os") or args.os or "Linux").strip()
        license_model = (r.get("license_model") or ("AWS" if row_cloud == "aws" else "BYOL")).strip()

        ebs_gb = as_float(r.get("ebs_gb"), 0.0)
        ebs_type = (r.get("ebs_type") or "gp3").strip()
        s3_gb = as_float(r.get("s3_gb"), 0.0)
        net_prof = (r.get("network_profile") or "").strip()
        db_engine = (r.get("db_engine") or "").strip()
        db_class = (r.get("db_instance_class") or "").strip()
        db_multi_az = as_bool(r.get("multi_az"), False)

        # BYOL → treat compute as Linux price
        os_for_compute = "Linux" if license_model.lower() == "byol" else os_row

        if not itype or not region_row:
            compute_price = None
            r["pricing_note"] = "Missing instance_type or region"
        else:
            if row_cloud == "azure":
                from pricing import azure_vm_price_hourly
                compute_price = azure_vm_price_hourly(region_row, itype, os_for_compute, license_model)
                r["pricing_note"] = r.get("pricing_note", "")
            else:
                compute_price = price_ec2_ondemand(itype, region_row, os_name=os_for_compute)
                r["pricing_note"] = r.get("pricing_note", "") if compute_price is not None else "No EC2 price found (check filters/region/OS)"

        # Monthly math
        hours = float(args.hours_per_month)
        compute_monthly = monthly_compute_cost(compute_price, hours)
        if getattr(args, "no_monthly", False):
            r["price_per_hour_usd"] = f"{compute_price:.6f}" if compute_price is not None else ""
            r["monthly_compute_usd"] = r["monthly_ebs_usd"] = r["monthly_s3_usd"] = ""
            r["monthly_network_usd"] = r["monthly_db_usd"] = r["monthly_total_usd"] = ""
        else:
            r["price_per_hour_usd"] = f"{compute_price:.6f}" if compute_price is not None else ""
            r["monthly_compute_usd"] = f"{compute_monthly:.2f}"
            r["monthly_ebs_usd"] = f"{monthly_ebs_cost(ebs_gb, ebs_type):.2f}"
            r["monthly_s3_usd"] = f"{monthly_s3_cost(s3_gb):.2f}"
            r["monthly_network_usd"] = f"{monthly_network_cost(net_prof):.2f}"
            if row_cloud == "aws" and db_engine and db_class and region_row:
                db_monthly = monthly_rds_cost(db_engine, db_class, region_row, license_model, db_multi_az, hours)
            else:
                db_monthly = 0.0
            r["monthly_db_usd"] = f"{db_monthly:.2f}"
            parts = [
                as_float(r["monthly_compute_usd"]),
                as_float(r["monthly_ebs_usd"]),
                as_float(r["monthly_s3_usd"]),
                as_float(r["monthly_network_usd"]),
                as_float(r["monthly_db_usd"]),
            ]
            r["monthly_total_usd"] = f"{sum(parts):.2f}"

        r["provider"] = row_cloud
        out_rows.append(r)

    fieldnames = list(out_rows[0].keys())
    for col in [
        "provider","price_per_hour_usd","monthly_compute_usd","monthly_ebs_usd",
        "monthly_s3_usd","monthly_network_usd","monthly_db_usd","monthly_total_usd","pricing_note"
    ]:
        if col not in fieldnames:
            fieldnames.append(col)

    out_path = make_output_path("price", args.output)
    print(f"Input:  {args.input}")
    print(f"Output: {out_path}")
    write_rows(out_path, out_rows, fieldnames)
    print(f"Wrote priced recommendations → {out_path}")

def parse_args():
    p = argparse.ArgumentParser(description="Recommend instance sizes (AWS/Azure) then price them. (CSV/Excel input)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("recommend", help="Recommend instance/VM sizes")
    p1.add_argument("--cloud", choices=["aws","azure"], required=True)
    p1.add_argument("--region", required=False, help="AWS region (for AWS rows), e.g., us-east-1")
    p1.add_argument("--in", dest="input", required=False)
    p1.add_argument("--sheet", required=False)
    p1.add_argument("--out", dest="output", required=False)
    p1.set_defaults(func=cmd_recommend)

    p2 = sub.add_parser("price", help="Add pricing to a recommendation CSV/Excel")
    p2.add_argument("--cloud", choices=["aws","azure"], required=True)
    p2.add_argument("--region", help="Default AWS region (if not present per-row)")
    p2.add_argument("--os", choices=["Linux","Windows","RHEL","SUSE"], default="Linux")
    p2.add_argument("--in", dest="input", required=False)
    p2.add_argument("--sheet", required=False)
    p2.add_argument("--out", dest="output", required=False)
    p2.add_argument("--latest", action="store_true")
    p2.add_argument("--hours-per-month", type=float, default=730.0)
    p2.add_argument("--no-monthly", action="store_true")
    p2.set_defaults(func=cmd_price)
    return p.parse_args()

if __name__ == "__main__":
    args = parse_args()
    args.func(args)

