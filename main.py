# main.py
import argparse, os, sys, datetime
from pathlib import Path
from typing import Optional, List
from recommender import (
    fetch_instance_catalog, infer_profile, pick_instance,
    smallest_meeting_cpu, smallest_meeting_mem
)
from pricing import (
    read_rows, write_rows, price_ec2_ondemand, price_rds_ondemand,
    monthly_compute_cost, monthly_ebs_cost, monthly_s3_cost,
    monthly_network_cost, monthly_rds_cost, find_latest_output,
    maybe_prompt_for_sheet, prompt_for_input_path, as_float, as_int, as_bool
)

def make_output_path(cmd: str, user_out: Optional[str] = None) -> str:
    if user_out:
        return user_out
    out_dir = Path("output"); out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return str(out_dir / f"{cmd}_{ts}.csv")

def cmd_recommend(args):
    # Region resolution (CLI -> AWS_REGION -> boto3 default)
    region = args.region or os.getenv("AWS_REGION")
    if not region:
        try:
            import boto3
            region = boto3.session.Session().region_name
        except Exception:
            pass
    if not region:
        raise SystemExit("Region not provided. Pass --region or set AWS_REGION / profile region.")

    # Input path + optional sheet prompt
    if not args.input:
        ipath = prompt_for_input_path()
        args.sheet = maybe_prompt_for_sheet(ipath, args.sheet)
        args.input = str(ipath)
    else:
        args.sheet = maybe_prompt_for_sheet(Path(args.input), args.sheet)

    catalog = fetch_instance_catalog(region)
    rows = read_rows(args.input, sheet=args.sheet)
    if not rows:
        raise SystemExit("❌ Input file has no rows.")

    out_rows: List[dict] = []
    for r in rows:
        try:
            rid = r["id"]; vcpu = int(r["vcpu"]); mem_gib = float(r["memory_gib"])
        except Exception:
            print(f"Skipping row with missing/invalid values: {r}", file=sys.stderr)
            continue

        prof = (str(r.get("profile","")).strip().lower() or "")
        if prof not in ("balanced","compute","memory"):
            prof = infer_profile(vcpu, mem_gib)

        chosen = pick_instance(catalog, prof, vcpu, mem_gib)

        overprov_vcpu = overprov_mem_gib = fit_reason = ""
        if chosen:
            overprov_vcpu = chosen["vcpu"] - vcpu
            overprov_mem_gib = round(chosen["memory_gib"] - mem_gib, 2)
            if chosen["vcpu"] == vcpu and round(chosen["memory_gib"],2) == round(mem_gib,2):
                fit_reason = "exact"
            else:
                cpu_only = smallest_meeting_cpu(catalog, vcpu)
                mem_only = smallest_meeting_mem(catalog, mem_gib)
                def rank(x): return (x["vcpu"], x["memory_gib"]) if x else (float("inf"), float("inf"))
                if cpu_only or mem_only:
                    fit_reason = "memory-bound" if rank(mem_only) >= rank(cpu_only) else "cpu-bound"
                else:
                    fit_reason = "no-fit-fallback"

        base = dict(r)
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
    all_keys = {k for row in out_rows for k in row.keys()}
    fieldnames = preferred + [k for k in all_keys if k not in preferred]
    write_rows(out_path, out_rows, fieldnames)
    print(f"Wrote recommendations → {out_path}")

def cmd_price(args):
    # Auto-pick latest recommend output when requested / missing
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

    rows = read_rows(args.input, sheet=args.sheet)
    if not rows:
        raise SystemExit("❌ Input file has no rows.")

    out_rows = []
    for r in rows:
        itype = r.get("recommended_instance_type") or r.get("instance_type") or ""
        region_row = r.get("region") or args.region
        os_row = (r.get("os") or args.os or "Linux").strip()
        license_model = (r.get("license_model") or "AWS").strip()
        ebs_gb = as_float(r.get("ebs_gb"), 0.0)
        ebs_type = (r.get("ebs_type") or "gp3").strip()
        s3_gb = as_float(r.get("s3_gb"), 0.0)
        net_prof = (r.get("network_profile") or "").strip()
        db_engine = (r.get("db_engine") or "").strip()
        db_class = (r.get("db_instance_class") or "").strip()
        db_multi_az = as_bool(r.get("multi_az"), False)

        os_for_compute = "Linux" if license_model.lower() == "byol" else os_row

        if not itype or not region_row:
            compute_price = None
            r["pricing_note"] = "Missing instance_type or region"
        else:
            compute_price = price_ec2_ondemand(itype, region_row, os_name=os_for_compute)
            r["pricing_note"] = r.get("pricing_note","") if compute_price is not None else "No EC2 price found (check filters/region/OS)"

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
            if db_engine and db_class and region_row:
                db_monthly = monthly_rds_cost(db_engine, db_class, region_row, license_model, db_multi_az, hours)
            else:
                db_monthly = 0.0
            r["monthly_db_usd"] = f"{db_monthly:.2f}"
            parts = [
                float(r["monthly_compute_usd"] or 0),
                float(r["monthly_ebs_usd"] or 0),
                float(r["monthly_s3_usd"] or 0),
                float(r["monthly_network_usd"] or 0),
                float(r["monthly_db_usd"] or 0),
            ]
            r["monthly_total_usd"] = f"{sum(parts):.2f}"

        out_rows.append(r)

    fieldnames = list(out_rows[0].keys())
    for col in ["price_per_hour_usd","monthly_compute_usd","monthly_ebs_usd","monthly_s3_usd",
                "monthly_network_usd","monthly_db_usd","monthly_total_usd","pricing_note"]:
        if col not in fieldnames: fieldnames.append(col)

    out_path = make_output_path("price", args.output)
    print(f"Input:  {args.input}")
    print(f"Output: {out_path}")
    write_rows(out_path, out_rows, fieldnames)
    print(f"Wrote priced recommendations → {out_path}")

def parse_args():
    p = argparse.ArgumentParser(description="Recommend AWS EC2 types then price them. (CSV/Excel input)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("recommend", help="Recommend EC2 instance types")
    p1.add_argument("--region", required=False, help="AWS region, e.g., us-east-1")
    p1.add_argument("--in", dest="input", required=False)
    p1.add_argument("--sheet", required=False)
    p1.add_argument("--out", dest="output", required=False)
    p1.set_defaults(func=cmd_recommend)

    p2 = sub.add_parser("price", help="Add On-Demand pricing to a recommendation CSV/Excel")
    p2.add_argument("--region", help="AWS region (optional if present per-row)")
    p2.add_argument("--os", choices=["Linux","Windows"], default="Linux")
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
