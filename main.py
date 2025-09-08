# main.py
import os
import sys
import time
import datetime
from pathlib import Path
from typing import Optional, List, Dict

import click
import pandas as pd

from validator import validate_dataframe, write_validator_report
from validator import AWS_REGIONS, AZURE_REGIONS

from recommender import (
    infer_profile,
    fetch_instance_catalog,           # AWS
    pick_instance,
    smallest_meeting_cpu,
    smallest_meeting_mem,
    fetch_azure_vm_catalog,           # Azure
    pick_azure_size,
    normalize_azure_region,           # Azure region normalizer
)

from pricing import (
    read_rows, write_rows,
    price_ec2_ondemand,
    monthly_compute_cost, monthly_ebs_cost, monthly_s3_cost,
    monthly_network_cost, monthly_rds_cost,
)

# If your project exposes Azure compute prices via pricing.azure_vm_price_hourly,
# import it; otherwise, stub gracefully and error only if used.
try:
    from pricing import azure_vm_price_hourly  # type: ignore
except Exception:  # pragma: no cover
    azure_vm_price_hourly = None


# ---------------------- Utilities ----------------------
def make_output_path(cmd: str, user_out: Optional[str] = None) -> str:
    """Return output path, ensuring ./output exists."""
    if user_out:
        return user_out
    out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return str(out_dir / f"{cmd}_{ts}.csv")


def find_latest_output(patterns=("output/recommend_*.csv", "output/recommend_*.xlsx")) -> Optional[Path]:
    """Find the most-recent recommendation output file."""
    from glob import glob
    candidates: List[str] = []
    for pat in patterns:
        candidates.extend(glob(pat))
    if not candidates:
        return None
    return max((Path(p) for p in candidates), key=lambda p: p.stat().st_mtime)


def as_float(x, default=0.0):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def as_bool(x, default=False):
    if isinstance(x, bool):
        return x
    if x is None:
        return default
    s = str(x).strip().lower()
    if s in {"y", "yes", "true", "1"}:
        return True
    if s in {"n", "no", "false", "0"}:
        return False
    return default


def cloud_from_str(s: Optional[str]) -> str:
    s = (s or "").strip().lower()
    if s in ("azure", "az"):
        return "azure"
    return "aws"


# ---------------------- CLI ----------------------
@click.group()
def cli():
    """Cloud Pricing Calculator CLI (Click-only)."""
    pass


# ---------------------- recommend ----------------------
@cli.command(name="recommend")
@click.option("--in", "in_path", required=True, help="Input CSV/Excel file.")
@click.option("--cloud", type=click.Choice(["aws", "azure"], case_sensitive=False), required=True)
@click.option("--region", required=False, help="AWS region for AWS runs (e.g., us-east-1). Azure uses per-row region or 'eastus' fallback.")
@click.option("--strict", is_flag=True, help="Fail if any row blocks recommendation or pricing (validator).")
@click.option("--validator-report", "validator_report_path", default=None,
              help="Path for validator report CSV (default: ./output/validator_report_<timestamp>.csv).")
@click.option("--output", "output_path", default=None, help="Output file path (CSV/Excel).")
def recommend_cmd(in_path, cloud, region, strict, validator_report_path, output_path):
    """
    Validate rows (no defaults). Recommend sizes for OK and REC_ONLY rows.
    Pricing is not performed here; use the 'price' command afterwards.
    """
    ts = time.strftime("%Y%m%d-%H%M%S")

    in_path = Path(in_path)
    if not in_path.exists():
        click.echo(f"ERROR: Input file not found: {in_path}", err=True)
        sys.exit(2)

    # Load input (first sheet by default for Excel)
    if in_path.suffix.lower() in {".xlsx", ".xls"}:
        df = pd.read_excel(in_path)
    else:
        df = pd.read_csv(in_path)

    # Optional overrides from CLI
    if cloud:
        df["cloud"] = cloud.lower()
    if region:
        df["region"] = region

    # ---- Validate (no defaults; report-only) ----
    ok_idx, rec_only_idx, error_idx, report_rows = validate_dataframe(df, input_file=str(in_path))
    if validator_report_path is None:
        validator_report_path = Path("output") / f"validator_report_{ts}.csv"
    write_validator_report(report_rows, str(validator_report_path))

    total = len(df)
    click.echo(f"Validation: rows={total} | ok={len(ok_idx)} | rec_only={len(rec_only_idx)} | error={len(error_idx)}")

    if strict and (len(error_idx) > 0 or len(rec_only_idx) > 0):
        click.echo(
            f"Strict mode: failing due to rows that block recommendation or pricing. See: {validator_report_path}",
            err=True,
        )
        sys.exit(2)

    # ---- Recommend (no pricing) ----
    default_cloud = cloud_from_str(cloud)
    aws_catalog = None  # lazy
    azure_catalog_by_region: Dict[str, Dict[str, dict]] = {}

    def recommend_row(row: dict) -> dict:
        rid = row.get("id", "")
        try:
            vcpu = int(row.get("vcpu"))
            mem_gib = float(row.get("memory_gib"))
        except Exception:
            return {
                **row,
                "id": rid,
                "cloud": default_cloud,
                "recommended_instance_type": "",
                "rec_vcpu": "",
                "rec_memory_gib": "",
                "overprov_vcpu": "",
                "overprov_mem_gib": "",
                "fit_reason": "",
                "note": "Invalid vcpu/memory_gib",
            }

        prof = (str(row.get("profile") or "").strip().lower())
        if prof not in ("balanced", "compute", "memory"):
            prof = infer_profile(vcpu, mem_gib)

        row_cloud = default_cloud  # enforce single-cloud run

        if row_cloud == "azure":
            az_region = normalize_azure_region(row.get("region") or "eastus")
            if az_region not in azure_catalog_by_region:
                azure_catalog_by_region[az_region] = fetch_azure_vm_catalog(az_region)
            chosen = pick_azure_size(azure_catalog_by_region[az_region], vcpu, mem_gib)
            out_region = az_region
        else:
            aws_region = row.get("region") or region
            if not aws_region:
                raise SystemExit("AWS region required for AWS recommendations. Use --region or provide per-row.")
            nonlocal aws_catalog  # capture outer reference
            if aws_catalog is None:
                aws_catalog = fetch_instance_catalog(aws_region)
            chosen = pick_instance(aws_catalog, prof, vcpu, mem_gib)
            out_region = aws_region

        overprov_vcpu = overprov_mem_gib = fit_reason = ""
        if chosen:
            overprov_vcpu = chosen["vcpu"] - vcpu
            overprov_mem_gib = round(chosen["memory_gib"] - mem_gib, 2)
            if chosen["vcpu"] == vcpu and round(chosen["memory_gib"], 2) == round(mem_gib, 2):
                fit_reason = "exact"
            elif row_cloud == "aws":
                cpu_only = smallest_meeting_cpu(aws_catalog, vcpu) if aws_catalog else None
                mem_only = smallest_meeting_mem(aws_catalog, mem_gib) if aws_catalog else None

                def rank(x): return (x["vcpu"], x["memory_gib"]) if x else (float("inf"), float("inf"))
                if cpu_only or mem_only:
                    fit_reason = "memory-bound" if rank(mem_only) >= rank(cpu_only) else "cpu-bound"
                else:
                    fit_reason = "no-fit-fallback"

        return {
            **row,
            "id": rid,
            "cloud": row_cloud,
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
            "note": "" if chosen else ("No matching size found in region." if row_cloud == "azure"
                                       else "No matching current-gen x86_64 found; consider GPU/ARM or older-gen."),
        }

    results: List[dict] = []
    for i in ok_idx + rec_only_idx:
        results.append(recommend_row(df.iloc[i].to_dict()))

    if not results:
        click.echo("No valid rows to output (all rows errored). See validator report.", err=True)
        sys.exit(2)

    out_df = pd.DataFrame(results)
    output_path = Path(output_path or (Path("output") / f"recommend_{ts}.csv"))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.suffix.lower() in {".xlsx", ".xls"}:
        with pd.ExcelWriter(output_path, engine="xlsxwriter") as writer:
            out_df.to_excel(writer, index=False, sheet_name="Results")
    else:
        out_df.to_csv(output_path, index=False)

    click.echo(f"Wrote recommendations -> {output_path}")
    click.echo(f"Wrote validator report -> {validator_report_path}")

@cli.command(name="list-aws-regions")
def list_aws_regions():
    """Print supported AWS region codes."""
    for r in sorted(AWS_REGIONS):
        click.echo(r)

@cli.command(name="list-azure-regions")
def list_azure_regions():
    """Print supported Azure region slugs."""
    for r in sorted(AZURE_REGIONS):
        click.echo(r)

# ---------------------- price ----------------------
@cli.command(name="price")
@click.option("--cloud", type=click.Choice(["aws", "azure"], case_sensitive=False), required=True,
              help="Cloud of the recommendation file to be priced.")
@click.option("--in", "in_path", required=False,
              help="Recommendation CSV/Excel to price. If omitted, will use --latest.")
@click.option("--latest", is_flag=True, help="Use the most recent recommend_* file from ./output.")
@click.option("--region", required=False, help="Default AWS region if missing in rows (e.g., us-east-1).")
@click.option("--os", "os_name", type=click.Choice(["Linux", "Windows", "RHEL", "SUSE"], case_sensitive=False),
              default="Linux", show_default=True)
@click.option("--hours-per-month", type=float, default=730.0, show_default=True)
@click.option("--no-monthly", is_flag=True, help="Write only price_per_hour, skip monthly breakdown.")
@click.option("--refresh-azure-prices", is_flag=True, help="Refresh Azure Retail Prices cache before pricing (if supported).")
@click.option("--output", "output_path", default=None, help="Output file path (CSV/Excel).")
def price_cmd(cloud, in_path, latest, region, os_name, hours_per_month, no_monthly, refresh_azure_prices, output_path):
    """
    Price the recommendation output. Enforces single-cloud file matching the --cloud argument.
    """
    expected_cloud = cloud_from_str(cloud)

    # Resolve input file
    rec_path: Optional[Path] = Path(in_path) if in_path else None
    if not rec_path:
        if latest:
            rec_path = find_latest_output()
            if not rec_path:
                raise SystemExit("❌ --latest set but no recommendation files found in ./output")
            print(f"ℹ️  Using latest recommendation file: {rec_path}")
        else:
            raise SystemExit("❌ Please provide --in <file> or use --latest.")

    if not rec_path.exists():
        raise SystemExit(f"❌ Input file not found: {rec_path}")

    # Read rows (read_rows already supports CSV/Excel; uses first sheet for Excel)
    rows = read_rows(str(rec_path), sheet=None)
    if not rows:
        raise SystemExit("❌ Input file has no rows.")

    # Validate single-cloud file
    seen = {(str(r.get("cloud", "")).strip().lower() or "") for r in rows}
    seen.discard("")  # allow older files with no 'cloud'; they inherit CLI cloud
    if seen:
        if len(seen) > 1 or next(iter(seen)) != expected_cloud:
            human = ", ".join(sorted(seen))
            raise SystemExit(
                f"❌ This price run is for '--cloud {expected_cloud}', but the file contains cloud={human}. "
                "Price with the matching --cloud or re-run 'recommend' for a single cloud."
            )

    out_rows: List[dict] = []
    for r in rows:
        row_cloud = expected_cloud
        r["cloud"] = expected_cloud  # make explicit in output

        itype = r.get("recommended_instance_type") or r.get("instance_type") or ""
        region_row = (r.get("region") or region or ("eastus" if row_cloud == "azure" else None))
        os_row = (r.get("os") or os_name or "Linux").strip()
        license_model = (r.get("license_model") or ("AWS" if row_cloud == "aws" else "BYOL")).strip()

        # BYOL → treat compute as Linux price component
        os_for_compute = "Linux" if license_model.lower() == "byol" else os_row

        # Compute hourly price
        if not itype or not region_row:
            compute_price = None
            r["pricing_note"] = "Missing instance_type or region"
        else:
            if row_cloud == "azure":
                if azure_vm_price_hourly is None:
                    raise SystemExit("❌ Azure pricing function not available: pricing.azure_vm_price_hourly")
                compute_price = azure_vm_price_hourly(
                    region_row, itype, os_for_compute, license_model, refresh=refresh_azure_prices
                )
                r["pricing_note"] = r.get("pricing_note", "")
            else:
                compute_price = price_ec2_ondemand(itype, region_row, os_name=os_for_compute)
                r["pricing_note"] = r.get("pricing_note", "") if compute_price is not None else \
                    "No EC2 price found (check filters/region/OS)"

        # Monthly math
        hours = float(hours_per_month)
        compute_monthly = monthly_compute_cost(compute_price, hours)

        if no_monthly:
            r["price_per_hour_usd"] = f"{compute_price:.6f}" if compute_price is not None else ""
            r["monthly_compute_usd"] = r["monthly_ebs_usd"] = r["monthly_s3_usd"] = ""
            r["monthly_network_usd"] = r["monthly_db_usd"] = r["monthly_total_usd"] = ""
        else:
            r["price_per_hour_usd"] = f"{compute_price:.6f}" if compute_price is not None else ""
            r["monthly_compute_usd"] = f"{compute_monthly:.2f}"
            # Optional inputs for storage/network/db; default to 0 when absent
            ebs_gb = as_float(r.get("ebs_gb"), 0.0)
            ebs_type = (r.get("ebs_type") or "gp3").strip()
            s3_gb = as_float(r.get("s3_gb"), 0.0)
            net_prof = (r.get("network_profile") or "").strip()

            r["monthly_ebs_usd"] = f"{monthly_ebs_cost(ebs_gb, ebs_type):.2f}"
            r["monthly_s3_usd"] = f"{monthly_s3_cost(s3_gb):.2f}"
            r["monthly_network_usd"] = f"{monthly_network_cost(net_prof):.2f}"

            if row_cloud == "aws":
                db_engine = (r.get("db_engine") or "").strip()
                db_class = (r.get("db_instance_class") or "").strip()
                db_multi_az = as_bool(r.get("multi_az"), False)
                if db_engine and db_class and region_row:
                    db_monthly = monthly_rds_cost(db_engine, db_class, region_row, license_model, db_multi_az, hours)
                else:
                    db_monthly = 0.0
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

    # Ensure consistent column order
    fieldnames = list(out_rows[0].keys())
    for col in [
        "provider", "price_per_hour_usd", "monthly_compute_usd", "monthly_ebs_usd",
        "monthly_s3_usd", "monthly_network_usd", "monthly_db_usd",
        "monthly_total_usd", "pricing_note"
    ]:
        if col not in fieldnames:
            fieldnames.append(col)

    out_path = make_output_path("price", output_path)
    print(f"Input:  {rec_path}")
    print(f"Output: {out_path}")
    write_rows(out_path, out_rows, fieldnames)
    print(f"Wrote priced recommendations → {out_path}")


# ---------------------- entry ----------------------
if __name__ == "__main__":
    cli()


