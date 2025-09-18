# main.py
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, List, Dict
import re

import click
import pandas as pd

try:
    from summary import write_run_summary
except ImportError:
    write_run_summary = None

# ---------------------- Imports from local modules ----------------------
from validator import (
    validate_dataframe,
    write_validator_report,
)
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

# Azure VM price function may be optional depending on your tree — import defensively.
try:
    from pricing import azure_vm_price_hourly  # type: ignore
except Exception:
    azure_vm_price_hourly = None  # type: ignore


# ---------------------- Small utilities ----------------------
def as_float(x, default=0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default

def as_bool(x, default=False) -> bool:
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


# ---------------------- Output helpers (date/timestamped folders) ----------------------
def _now_date_time():
    import datetime as _dt
    d = _dt.datetime.now()
    return d.strftime("%Y-%m-%d"), d.strftime("%H%M%S")

def _ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p

def _derive_run_dir_from_recommend(rec_path: Path) -> Optional[Path]:
    """
    If the recommend file already lives in output/YYYY-MM-DD/HHMMSS/,
    reuse that directory. Otherwise return None.
    """
    try:
        parent = rec_path.resolve().parent
        date_dir = parent.parent.name
        time_dir = parent.name
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_dir) and re.fullmatch(r"\d{6}", time_dir):
            return parent
    except Exception:
        pass
    return None

def _new_run_dir(base: Path = Path("output")) -> Path:
    date_str, time_str = _now_date_time()
    return _ensure_dir(base / date_str / time_str)

def _default_paths_for_recommend(user_out: Optional[str], user_report: Optional[str]) -> tuple[Path, Path]:
    """
    Decide file paths for recommend + validator report.

    If user provided explicit output paths, honor them (don’t force folders).
    Otherwise create output/YYYY-MM-DD/HHMMSS/{recommend.csv, validator_report.csv}.
    """
    if user_out or user_report:
        rec_path = Path(user_out) if user_out else None
        rep_path = Path(user_report) if user_report else None
        if rec_path:
            rec_path.parent.mkdir(parents=True, exist_ok=True)
        if rep_path:
            rep_path.parent.mkdir(parents=True, exist_ok=True)
        # If only one provided, place the sibling next to it.
        if rec_path and not rep_path:
            rep_path = rec_path.with_name("validator_report.csv")
        if rep_path and not rec_path:
            rec_path = rep_path.parent / "recommend.csv"
        return rec_path, rep_path  # type: ignore[return-value]

    run_dir = _new_run_dir()
    return run_dir / "recommend.csv", run_dir / "validator_report.csv"

def _default_path_for_price(rec_path: Optional[Path], user_out: Optional[str]) -> Path:
    """
    Price output path. If user_out provided, honor it.
    Else, if rec_path is inside a timestamped folder, reuse that folder.
    Else, create a fresh timestamped folder.
    """
    if user_out:
        out = Path(user_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        return out

    reuse_dir = _derive_run_dir_from_recommend(rec_path) if rec_path else None
    run_dir = reuse_dir if reuse_dir else _new_run_dir()
    return run_dir / "price.csv"

def find_latest_output(patterns: Optional[List[str]] = None) -> Optional[Path]:
    """
    Find the most-recent *recommend* output file.

    Supports new nested layout:
        output/YYYY-MM-DD/HHMMSS/recommend.csv|xlsx|xls
    and legacy flat layout:
        output/recommend_*.csv|xlsx|xls
    """
    from glob import glob

    if patterns is None:
        patterns = [
            # New nested layout
            "output/**/recommend.csv",
            "output/**/recommend.xlsx",
            "output/**/recommend.xls",
            # Legacy flat files
            "output/recommend_*.csv",
            "output/recommend_*.xlsx",
            "output/recommend_*.xls",
        ]

    candidates: List[str] = []
    for pat in patterns:
        candidates.extend(glob(pat, recursive=True))

    if not candidates:
        return None

    return max((Path(p) for p in candidates), key=lambda p: p.stat().st_mtime)


# ---------------------- CLI root ----------------------
@click.group()
def cli():
    """Cloud Pricing Calculator CLI."""
    pass


# ---------------------- list regions (helpers) ----------------------
@cli.command(name="list-aws-regions")
def list_aws_regions():
    """Print supported AWS region codes."""
    try:
        from validator import AWS_REGIONS
        regions = sorted(AWS_REGIONS)
    except Exception:
        regions = []
    if not regions:
        click.echo("No AWS regions table available in validator.py")
    else:
        for r in regions:
            click.echo(r)

@cli.command(name="list-azure-regions")
def list_azure_regions():
    """Print supported Azure region slugs."""
    try:
        from validator import AZURE_REGIONS
        regions = sorted(AZURE_REGIONS)
    except Exception:
        regions = []
    if not regions:
        click.echo("No Azure regions table available in validator.py")
    else:
        for r in regions:
            click.echo(r)


# ---------------------- recommend ----------------------
@cli.command(name="recommend")
@click.option("--in", "in_path", required=True, help="Input CSV/Excel file.")
@click.option("--cloud", type=click.Choice(["aws", "azure"], case_sensitive=False), required=True)
@click.option("--region", required=False, help="AWS region for AWS runs (e.g., us-east-1). Azure uses per-row region.")
@click.option("--strict", is_flag=True, help="Fail (non-zero) if any row is rec_only or error.")
@click.option("--validator-report", "validator_report_path", default=None,
              help="Path for validator report CSV (default: run folder).")
@click.option("--output", "output_path", default=None, help="Output file path (CSV/Excel) for recommendations.")
def recommend_cmd(in_path, cloud, region, strict, validator_report_path, output_path):
    """
    Validate rows (no defaults). Recommend sizes for OK and REC_ONLY rows.
    Pricing is not performed here; use the 'price' command afterwards.
    """
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

    # Azure preflight (fail fast) — only if running Azure
    if cloud_from_str(cloud) == "azure":
        try:
            from azure_preflight import ensure_azure_ready, AzurePreflightError  # type: ignore
            ensure_azure_ready()
        except Exception as e:
            click.echo(f"❌ Azure preflight failed: {e}", err=True)
            sys.exit(2)

    # ---- Validate (no defaults; report-only) ----
    ok_idx, rec_only_idx, error_idx, report_rows = validate_dataframe(df, input_file=str(in_path))

    # Build default output paths in a date/timestamped run folder (unless user overrides)
    rec_out_path, rep_out_path = _default_paths_for_recommend(output_path, validator_report_path)

    write_validator_report(report_rows, str(rep_out_path))

    total = len(df)
    click.echo(f"Validation: rows={total} | ok={len(ok_idx)} | rec_only={len(rec_only_idx)} | error={len(error_idx)}")

    if strict and (len(error_idx) > 0 or len(rec_only_idx) > 0):
        click.echo(
            f"Strict mode: failing due to rows that block recommendation or pricing. See: {rep_out_path}",
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

    # Write to CSV or Excel at the chosen path (same run folder as validator)
    rec_out = Path(rec_out_path)
    rep_out = Path(rep_out_path)
    rec_out.parent.mkdir(parents=True, exist_ok=True)

    if rec_out.suffix.lower() in {".xlsx", ".xls"}:
        with pd.ExcelWriter(rec_out, engine="xlsxwriter") as writer:
            out_df.to_excel(writer, index=False, sheet_name="Results")
    else:
        out_df.to_csv(rec_out, index=False)

    click.echo(f"Wrote recommendations -> {rec_out}")
    click.echo(f"Wrote validator report -> {rep_out}")

    if write_run_summary:
        try:
            run_dir = Path(rec_out).parent
            write_run_summary(run_dir, rec_out, None)
            # write_run_summary(Path(rec_out).parent, rec_out, None)
        except Exception as e:
            click.echo(f"⚠️ Summary generation failed: {e}", err=True)

# ---------------------- price ----------------------
@cli.command(name="price")
@click.option("--cloud", type=click.Choice(["aws", "azure"], case_sensitive=False), required=True,
              help="Cloud of the recommendation file to be priced.")
@click.option("--in", "in_path", required=False,
              help="Recommendation CSV/Excel to price. If omitted, will use --latest.")
@click.option("--latest", is_flag=True, help="Use the most recent recommend file from ./output (nested folders supported).")
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

    # --- Resolve input recommend file ---
    rec_path: Optional[Path] = Path(in_path) if in_path else None
    if not rec_path:
        if latest:
            rec_path = find_latest_output()
            if not rec_path:
                raise SystemExit("❌ --latest set but no recommendation files found under ./output")
            print(f"ℹ️  Using latest recommendation file: {rec_path}")
        else:
            raise SystemExit("❌ Please provide --in <file> or use --latest.")

    if not rec_path.exists():
        raise SystemExit(f"❌ Input file not found: {rec_path}")

    # --- Decide price output path (same run folder as recommend if possible) ---
    price_out_path = _default_path_for_price(rec_path, output_path)

    # --- Read and validate rows ---
    rows = read_rows(str(rec_path), sheet=None)
    if not rows:
        raise SystemExit("❌ Input file has no rows.")

    seen = {(str(r.get("cloud", "")).strip().lower() or "") for r in rows}
    seen.discard("")
    if seen:
        if len(seen) > 1 or next(iter(seen)) != expected_cloud:
            human = ", ".join(sorted(seen))
            raise SystemExit(
                f"❌ This price run is for '--cloud {expected_cloud}', but the file contains cloud={human}. "
                "Price with the matching --cloud or re-run 'recommend' for a single cloud."
            )

    # --- Pricing loop ---
    out_rows: List[dict] = []
    for r in rows:
        row_cloud = expected_cloud
        r["cloud"] = expected_cloud  # make explicit in output

        itype = r.get("recommended_instance_type") or r.get("instance_type") or ""
        region_row = (r.get("region") or region or ("eastus" if row_cloud == "azure" else None))
        os_row = (r.get("os") or os_name or "Linux").strip()
        lm1 = r.get("license_model")
        license_model = (r.get("license_model.1") or lm1 or ("AWS" if row_cloud == "aws" else "BYOL")).strip()

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
                    # ---- Normalize engine for AWS Pricing API ----
                    _eng_map = {
                        "mysql": "MySQL",
                        "mariadb": "MariaDB",
                        "postgres": "PostgreSQL",
                        "postgresql": "PostgreSQL",
                        "oracle": "Oracle",
                        "sqlserver": "SQL Server",
                        "sql server": "SQL Server",
                    }
                    eng_key = db_engine.strip().lower()
                    db_engine_norm = _eng_map.get(eng_key, db_engine)

                    # ---- License handling by engine ----
                    lm_in = (r.get("license_model") or "").strip()
                    # Default to License included for engines that don't support BYOL on RDS
                    if db_engine_norm in {"MySQL", "PostgreSQL", "MariaDB", "SQL Server"}:
                        license_model = "AWS"  # our code maps this to "License included"
                        if eng_key == "sqlserver" and lm_in.lower() == "byol":
                            r["pricing_note"] = (r.get("pricing_note","") + " | RDS SQL forces License-Included").strip(" |")
                    # Oracle keeps whatever user provided (BYOL or LI)
                    # db_monthly = monthly_rds_cost(db_engine, db_class, region_row, license_model, db_multi_az, hours)
                    db_monthly = monthly_rds_cost(db_engine_norm, db_class, region_row, license_model, db_multi_az, hours)
                else:
                    db_monthly = 0.0
            elif row_cloud == "azure":
                # ---------- Azure SQL DB / Managed Instance (SQL Server only) ----------
                db_monthly = 0.0
                try:
                    db_engine = (r.get("db_engine") or "").strip().lower()
                    if db_engine in {"sqlserver", "sql server"}:
                        from pricing import monthly_azure_sql_cost
                        deployment = (r.get("db_deployment") or "single").strip().lower()   # "single" or "mi"
                        if deployment not in {"single", "mi"}:
                            deployment = "single"
                        tier = (r.get("db_tier") or "GeneralPurpose").strip()
                        family = (r.get("db_family") or "").strip() or None
                        # Default vCores/storage if omitted
                        v_raw = r.get("db_vcores")
                        vcores = int(v_raw) if str(v_raw).strip().lower() not in {"", "none", "null"} else 8
                        storage_gb = as_float(r.get("db_storage_gb"), 128.0)
                        # Azure: use the already-normalized license_model from above in this loop
                        lm = (license_model or "").strip().lower()
                        license_model_az = "AHUB" if lm in {"byol", "ahub", "azure hybrid benefit", "hybrid"} else "LicenseIncluded"
                        db_monthly = monthly_azure_sql_cost(
                            deployment=deployment,
                            region=str(region_row or "eastus"),
                            tier=tier,
                            family=family,
                            vcores=float(vcores),
                            storage_gb=storage_gb,
                            license_model=license_model_az,
                            hours=float(hours),
                        )
                       # Debug: show pure heuristic LI vs AHUB (ignore overrides for clarity)
                        li_monthly  = monthly_azure_sql_cost(deployment, str(region_row or "eastus"), tier, family,
                                               float(vcores), storage_gb, "LicenseIncluded", float(hours),
                                               use_overrides=False)

                        ahub_monthly = monthly_azure_sql_cost(deployment, str(region_row or "eastus"), tier, family, float(vcores), storage_gb, "AHUB", float(hours),use_overrides=False)
                        r["pricing_note"] = (r.get("pricing_note","") +
                        f" | Azure SQL {deployment} {tier} {vcores} vC, {storage_gb} GB, "
                        f"effective_license={license_model_az} li=${li_monthly:.2f} ahub=${ahub_monthly:.2f}").strip(" |")

                except Exception as e:
                    r["pricing_note"] = (r.get("pricing_note","") + f" | DB pricing skipped: {e}").strip(" |")
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

    # --- Write file ---
    fieldnames = list(out_rows[0].keys())
    for col in [
        "provider", "price_per_hour_usd", "monthly_compute_usd", "monthly_ebs_usd",
        "monthly_s3_usd", "monthly_network_usd", "monthly_db_usd",
        "monthly_total_usd", "pricing_note"
    ]:
        if col not in fieldnames:
            fieldnames.append(col)

    out_path = Path(price_out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Input:  {rec_path}")
    print(f"Output: {out_path}")
    write_rows(str(out_path), out_rows, fieldnames)
    print(f"Wrote priced recommendations → {out_path}")

    # Create Excel workbook with 'All' + per-environment tabs + optional Summary
    try:
        df_all = pd.DataFrame(out_rows)
        _write_pricing_excel_workbook(out_path, df_all)
    except Exception as e:
        print(f"⚠️ Excel workbook generation skipped: {e}")

    if write_run_summary:
        try:
            run_dir = Path(out_path).parent
            write_run_summary(run_dir, None, out_path)
            # write_run_summary(Path(out_path).parent, None, out_path)
        except Exception as e:
            print(f"⚠️ Summary generation failed: {e}")

# ---------------------- Excel output helpers ----------------------
def _sanitize_sheet_name(name: str) -> str:
    bad = '[]:*?/\\'
    s = str(name or "").strip() or "Unspecified"
    for ch in bad:
        s = s.replace(ch, "-")
    if len(s) > 31:
        s = s[:31]
    if s.startswith("'") or s.endswith("'"):
        s = s.strip("'")
    return s or "Unspecified"

def _autosize_and_style(writer, df, sheet_name: str):
    ws = writer.sheets[sheet_name]
    try:
        bold = writer.book.add_format({"bold": True})
        ws.set_row(0, None, bold)
    except Exception:
        pass
    try:
        ws.freeze_panes(1, 0)
    except Exception:
        pass
    for idx, col in enumerate(df.columns):
        try:
            max_len = max(len(str(col)), *(len(str(x)) for x in df[col].astype(str).tolist()))
            ws.set_column(idx, idx, min(max_len + 2, 60))
        except Exception:
            continue

def _detect_environment_column(df) -> str | None:
    for c in df.columns:
        if str(c).strip().lower() in {"environment", "env"}:
            return c
    return None

def _write_pricing_excel_workbook(price_csv_path: Path, all_rows_df):
    out_xlsx = price_csv_path.with_suffix(".xlsx")
    run_dir = price_csv_path.parent
    summary_csv = run_dir / "summary.csv"

    env_col = _detect_environment_column(all_rows_df)
    if env_col:
        env_series = all_rows_df[env_col].fillna("Unspecified").astype(str)
    else:
        env_series = None

    with pd.ExcelWriter(out_xlsx, engine="xlsxwriter") as writer:
        all_rows_df.to_excel(writer, index=False, sheet_name="All")
        _autosize_and_style(writer, all_rows_df, "All")

        if env_series is not None:
            for env_value in sorted(set(env_series)):
                sub = all_rows_df[env_series == env_value]
                sheet = _sanitize_sheet_name(env_value)
                used = set(k for k in writer.sheets.keys())
                if sheet in used:
                    base = sheet
                    i = 2
                    while sheet in used and i < 100:
                        suffix = f"_{i}"
                        sheet = (base[:31-len(suffix)] + suffix)[:31]
                        i += 1
                sub.to_excel(writer, index=False, sheet_name=sheet)
                _autosize_and_style(writer, sub, sheet)

        if summary_csv.exists():
            try:
                df_summary = pd.read_csv(summary_csv)
                df_summary.to_excel(writer, index=False, sheet_name="Summary")
                _autosize_and_style(writer, df_summary, "Summary")
            except Exception:
                pass
    print(f"Wrote Excel workbook with environment tabs → {out_xlsx}")

# ---------------------- entry ----------------------
if __name__ == "__main__":
    cli()
