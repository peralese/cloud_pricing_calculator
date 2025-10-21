from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, List, Dict
import re
from glob import glob

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
    # Azure DB pricing helpers (already implemented in pricing.py)
    monthly_azure_sql_cost,
)

# Azure VM price function may be optional depending on your tree — import defensively.
try:
    from pricing import azure_vm_price_hourly  # type: ignore
except Exception:
    azure_vm_price_hourly = None  # type: ignore

# Baseline module (prompt-driven)
try:
    from baseline import (
        prompt_for_inputs as baseline_prompt,
        resolve_rates   as baseline_rates,
        compute_baseline,
        write_baseline_csv,
    )
except Exception:
    baseline_prompt = baseline_rates = compute_baseline = write_baseline_csv = None  # type: ignore

# ---------------------- Small utilities ----------------------
def as_float(x, default=0.0) -> float:
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default

def as_float_opt(x) -> float | None:
    """Parse to float or return None when not provided/invalid."""
    try:
        if x is None or str(x).strip() == "":
            return None
        return float(x)
    except Exception:
        return None

def as_int(x, default=0) -> int:
    try:
        if x is None or x == "":
            return default
        return int(float(x))
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

def _find_baseline_csv(preferred_dir: Path) -> Optional[Path]:
    """
    Prefer baseline.csv in the current run folder. If not found,
    fall back to the most-recent baseline.csv anywhere under ./output.
    """
    p = preferred_dir / "baseline.csv"
    if p.exists():
        return p
    from glob import glob
    candidates = glob("output/**/baseline.csv", recursive=True)
    if not candidates:
        return None
    return max((Path(c) for c in candidates), key=lambda q: q.stat().st_mtime)

# ---------------------- CLI root ----------------------
@click.group()
def cli():
    """Cloud Pricing Calculator CLI."""
    pass

@cli.command(name="baseline")
@click.option("--cloud", type=click.Choice(["aws"], case_sensitive=False), required=True,
              help="Only 'aws' is supported for baseline at this time.")
def baseline_cmd(cloud):
    """
    Prompt-driven baseline cost capture for AWS VPC networking:
      - Transit Gateway (attachments + data)
      - Interface Endpoints / PrivateLink (per-AZ counts + data)
    Defaults per your guidance:
      attachments=1, tgw_data_gb=100, base_endpoints_per_az=8, azs=2, vpce_data_gb defaults to tgw_data_gb.
    """
    if (cloud or "").lower() != "aws":
        click.echo("ERROR: baseline currently supports only AWS.", err=True)
        sys.exit(2)
    if not (baseline_prompt and baseline_rates and compute_baseline and write_baseline_csv):
        click.echo("ERROR: baseline module unavailable (baseline.py not found or import failed).", err=True)
        sys.exit(2)

    # Interactive prompts → compute → write baseline.csv into a fresh run folder
    inputs = baseline_prompt()
    rates  = baseline_rates(inputs.region)
    rows, total = compute_baseline(inputs, rates)
    # Prefer the latest recommend run folder so all artifacts stay together
    latest_rec = find_latest_output()
    run_dir = latest_rec.parent if latest_rec else _new_run_dir()
    out_csv = write_baseline_csv(run_dir, rows)
    click.echo(f"Wrote baseline → {out_csv}")

    # Opportunistically refresh summary artifacts for that run folder
    if write_run_summary:
        try:
            write_run_summary(run_dir, None, None)
        except Exception as e:
            click.echo(f"⚠️ Summary generation failed: {e}", err=True)

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
        except Exception as e:
            click.echo(f"⚠️ Summary generation failed: {e}", err=True)

# ---------------------- validate ----------------------
@cli.command(name="validate")
@click.option("--in", "in_path", required=True, help="Input CSV/Excel file.")
@click.option("--output", "output_path", default=None, help="Output path for validator report CSV.")
@click.option("--strict", is_flag=True, help="Fail (non-zero) if any row is rec_only or error.")
def validate_cmd(in_path, output_path, strict):
    """
    Validate input rows and write a validator report CSV. Does not recommend or price.
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

    # Validate (no defaults; report-only)
    ok_idx, rec_only_idx, error_idx, report_rows = validate_dataframe(df, input_file=str(in_path))

    # Choose report output path (timestamped folder if not provided)
    _, rep_out_path = _default_paths_for_recommend(None, output_path)
    write_validator_report(report_rows, str(rep_out_path))

    total = len(df)
    click.echo(f"Validation: rows={total} | ok={len(ok_idx)} | rec_only={len(rec_only_idx)} | error={len(error_idx)}")
    click.echo(f"Wrote validator report -> {rep_out_path}")

    if strict and (len(error_idx) > 0 or len(rec_only_idx) > 0):
        click.echo(
            "Strict mode: failing due to rows that block recommendation or pricing.",
            err=True,
        )
        sys.exit(2)

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
@click.option("--no-auto-recommend", is_flag=True, default=False, help="Disable automatic recommendation for missing required fields; fail instead.")
def price_cmd(cloud, in_path, latest, region, os_name, hours_per_month, no_monthly, refresh_azure_prices, output_path, no_auto_recommend):
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

    # --- AWS baseline (optional, auto-prompt) ---
    # If we're pricing AWS and the current run folder does not have a baseline.csv,
    # prompt the user for baseline inputs and write it alongside price output.
    # This restores the previous interactive flow the user expected.
    try:
        run_dir = price_out_path.parent
        baseline_here = run_dir / "baseline.csv"
        if expected_cloud == "aws" and not baseline_here.exists():
            if baseline_prompt and baseline_rates and compute_baseline and write_baseline_csv:
                click.echo("Collecting AWS baseline inputs (TGW, VPC endpoints, GitRunner, S3)...")
                b_inputs = baseline_prompt()
                b_rates = baseline_rates(b_inputs.region)
                b_rows, _ = compute_baseline(b_inputs, b_rates)
                b_out = write_baseline_csv(run_dir, b_rows)
                click.echo(f"Wrote baseline -> {b_out}")
            else:
                click.echo("Baseline module unavailable; skipping baseline prompts.")
    except Exception as e:
        click.echo(f"?? Baseline prompt skipped: {e}")

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

    # --- Read and validate rows ---
    rows = read_rows(str(rec_path), sheet=None)
    if not rows:
        raise SystemExit("❌ Input file has no rows.")

    # Auto-recommend missing fields unless --no-auto-recommend
    if not no_auto_recommend:
        def needs_reco(r: dict) -> bool:
            cloud_lc = (r.get("cloud") or expected_cloud).strip().lower()
            engine = (r.get("db_engine") or "").strip().lower()
            # If we expect to price RDS SQL Server, we need db_instance_class & storage and license_included
            if cloud_lc == "aws" and ("sql" in engine and "server" in engine):
                if not r.get("db_instance_class"):
                    return True
                if not r.get("db_storage_gb"):
                    return True
                lic = (r.get("license_model") or "").strip().lower()
                if lic and lic not in {"license_included", "license-included"}:
                    return True
            # Also, for regular VM compute pricing we need an instance type
            itype = r.get("recommended_instance_type") or r.get("instance_type")
            if not itype:
                return True
            return False

        mask = [needs_reco(r) for r in rows]
        if any(mask):
            import pandas as _pd
            from recommender import (
                infer_profile, fetch_instance_catalog, pick_instance,
                fetch_azure_vm_catalog, pick_azure_size, normalize_azure_region
            )

            df = _pd.DataFrame(rows)
            default_cloud = expected_cloud

            # Only recommend for the subset that needs it
            to_fix = df[_pd.Series(mask)].copy()

            if default_cloud == "aws":
                # Determine a fallback region from CLI or any non-empty row value
                fallback_region = region or next(
                    (str(r.get("region")).strip() for r in rows if str(r.get("region") or "").strip()),
                    None,
                )

                # Get the region series if present; do NOT truth-test the Series
                reg_series = to_fix["region"] if "region" in to_fix.columns else _pd.Series([], index=to_fix.index)

                # First non-empty region in the subset, else fallback_region
                non_empty = (
                    reg_series.astype(str)
                    .str.strip()
                    .replace({"": _pd.NA})
                    .dropna()
                )
                aws_region = (non_empty.iloc[0] if not non_empty.empty else fallback_region)

                if not aws_region:
                    raise SystemExit("AWS region required for auto-recommend. Use --region or provide per-row.")

                cat = fetch_instance_catalog(str(aws_region))


                def _reco_row(r):
                    vcpu = int(float(r.get("vcpu", 0)))
                    mem  = float(r.get("memory_gib", 0.0))
                    prof = (str(r.get("profile") or "").strip().lower()) or infer_profile(vcpu, mem)
                    chosen = pick_instance(cat, prof, vcpu, mem)
                    r["recommended_instance_type"] = chosen["instanceType"] if chosen else r.get("recommended_instance_type","")
                    # For RDS SQL Server, if class missing, reuse instance type as class fallback when sensible
                    eng = (str(r.get("db_engine") or "")).strip().lower()
                    if eng and not r.get("db_instance_class") and chosen:
                        it = chosen["instanceType"]
                        r["db_instance_class"] = it if it.startswith("db.") else f"db.{it}"
                    return r

                to_fix = to_fix.apply(_reco_row, axis=1)

            else:  # azure
                def _reco_row(r):
                    vcpu = int(float(r.get("vcpu", 0)))
                    mem  = float(r.get("memory_gib", 0.0))
                    azr  = normalize_azure_region(r.get("region") or "eastus")
                    cat  = fetch_azure_vm_catalog(azr)
                    chosen = pick_azure_size(cat, vcpu, mem)
                    r["region"] = azr
                    r["recommended_instance_type"] = chosen["instanceType"] if chosen else r.get("recommended_instance_type","")
                    return r

                to_fix = to_fix.apply(_reco_row, axis=1)

            # Merge back only the columns we set
            for i, need in enumerate(mask):
                if need:
                    rows[i].update({k: v for k, v in to_fix.iloc[0].to_dict().items() if k in {
                        "recommended_instance_type", "db_instance_class", "region"
                    }})
                    to_fix = to_fix.iloc[1:]  # consume one

    else:
        # Strict mode: fail fast if required bits are missing
        bad = []
        for r in rows:
            itype = r.get("recommended_instance_type") or r.get("instance_type")
            dbeng = (r.get("db_engine") or "").lower()
            lic   = (r.get("license_model") or "").lower()
            if not itype:
                bad.append(("missing instance_type/recommended_instance_type", r.get("id","")))
            if "sql" in dbeng and "server" in dbeng:
                if not r.get("db_instance_class"):
                    bad.append(("missing db_instance_class for RDS SQL Server", r.get("id","")))
                if lic and lic not in {"license_included","license-included"}:
                    bad.append(("RDS SQL Server requires license_included", r.get("id","")))
        if bad:
            msg = "\n".join(f"- {why} (row id: {rid})" for why, rid in bad)
            raise SystemExit("Strict mode (--no-auto-recommend) failed:\n" + msg)


    # --- Pricing loop ---
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
                    str(region_row), itype, os_for_compute, license_model, refresh=refresh_azure_prices
                )
                r["pricing_note"] = r.get("pricing_note", "")
            else:
                compute_price = price_ec2_ondemand(itype, str(region_row), os_name=os_for_compute)
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

            def _normalize_rds_class(cls: str) -> str:
                cls = str(cls or "").strip()
                return cls if cls.startswith("db.") else (f"db.{cls}" if cls else cls)

            # ----- Database monthly cost -----
            # --- inside the AWS DB monthly cost block in main.py ---
            db_engine = (r.get("db_engine") or "").strip()
            db_class = (r.get("db_instance_class") or "").strip()
            db_multi_az = as_bool(r.get("multi_az"), False)

            # Write what we resolved so you can see it in the “All” tab
            if db_class:
                r["resolved_db_instance_class"] = db_class
            elif db_engine:
                r["resolved_db_instance_class"] = ""  # explicit blank

            # If db_instance_class is missing, try to derive from recommended/compute type
            if db_engine and not db_class:
                candidate = (r.get("recommended_instance_type") or r.get("instance_type") or "").strip()
                if candidate:
                    # strip leading "db." if someone already provided it
                    cand = candidate[3:] if candidate.startswith("db.") else candidate
                    fam, _, size = cand.partition(".")
                    eng_l = db_engine.lower()
                    fam_l = fam.lower()

                    # 1) General fallback: map compute-only families to closest RDS families
                    #    This helps Postgres/MySQL too (not just SQL Server).
                    general_fallback = {
                        "c7i": "m7i", "c7g": "m7g",
                        "c6i": "m6i", "c6g": "m6g",
                        "c5": "m5",   "c5n": "m5", "c4": "m4",
                    }
                    fam2 = general_fallback.get(fam_l, fam)

                    # 2) SQL Server special fallback (some 7-series aren’t offered yet)
                    if "sql" in eng_l and "server" in eng_l:
                        down = {"m7i":"m6i", "r7i":"r6i", "m7g":"m6i", "r7g":"r6i"}
                        fam2 = down.get(fam2.lower(), ("m6i" if fam2.lower().startswith("c") else fam2))

                    db_class = f"db.{fam2}.{size}" if size else f"db.{fam2}"

            # Final guard: only attempt pricing when we have engine, class and region
            if db_engine and db_class and region_row:
                db_monthly = monthly_rds_cost(db_engine, db_class, str(region_row), license_model, db_multi_az, hours)
            else:
                db_monthly = 0.0


                # Azure SQL DB/Managed Instance (Option B)
                def _first(*names):
                    for n in names:
                        v = r.get(n)
                        if v not in (None, ""):
                            return v
                    return None

                az_dep = (str(_first("az_sql_deployment", "db_deployment") or "")).strip().lower()  # "single" | "mi"
                if az_dep in {"single", "mi"}:
                    az_tier    = (str(_first("az_sql_tier", "db_tier") or "GeneralPurpose")).strip()
                    az_family  = (str(_first("az_sql_family", "db_family") or "")).strip() or None  # e.g., "Gen5" or blank
                    az_vcores  = as_float(_first("az_sql_vcores", "db_vcores"), 0.0)
                    # IMPORTANT: use your shared column here
                    az_storage = as_float(_first("az_sql_storage_gb", "db_storage_gb"), 0.0)

                    # Prefer explicit Azure-style license model; else map generic license_model
                    lic_raw = (str(_first("az_sql_license_model", "license_model") or "LicenseIncluded")).strip()
                    # Map BYOL -> AHUB for Azure SQL semantics
                    az_lic = "AHUB" if lic_raw.upper() in {"BYOL", "AHUB"} else "LicenseIncluded"

                    if (az_vcores > 0 or az_storage > 0) and region_row:
                        try:
                            db_monthly += monthly_azure_sql_cost(
                                "mi" if az_dep == "mi" else "single",
                                str(region_row),
                                az_tier,
                                az_family,
                                az_vcores,
                                az_storage,  # includes backup/storage in our model
                                az_lic,
                                hours,
                            )
                        except Exception:
                            pass  # keep pricing robust


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
    # Build fieldnames from the union of keys across all rows so DictWriter won't choke
    fn_set = set()
    for r in out_rows:
        fn_set.update(r.keys())

    # Keep a readable order for common columns, then append any others
    preferred_order = [
        "id", "name", "cloud", "region", "environment", "profile",
        "recommended_instance_type", "instance_type",
        "db_engine", "db_instance_class", "resolved_db_instance_class",
        "license_model", "multi_az",
        "vcpu", "memory_gib", "ebs_gb", "ebs_type", "s3_gb", "network_profile",
        "provider", "price_per_hour_usd", "monthly_compute_usd", "monthly_ebs_usd",
        "monthly_s3_usd", "monthly_network_usd", "monthly_db_usd",
        "monthly_total_usd", "pricing_note"
    ]
    fieldnames = [c for c in preferred_order if c in fn_set] + [c for c in sorted(fn_set) if c not in preferred_order]

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

# ---------------------- Excel output helpers ----------------------
def _write_pricing_excel_workbook(price_csv_path: Path, all_rows_df: pd.DataFrame):
    out_xlsx = price_csv_path.with_suffix(".xlsx")
    run_dir = price_csv_path.parent
    summary_csv = run_dir / "summary.csv"
    baseline_csv = _find_baseline_csv(run_dir)

    env_col = _detect_environment_column(all_rows_df)
    env_series = (
        all_rows_df[env_col].fillna("Unspecified").astype(str)
        if env_col else None
    )

    with pd.ExcelWriter(out_xlsx, engine="xlsxwriter") as writer:
        # All rows
        all_rows_df.to_excel(writer, index=False, sheet_name="All")
        _autosize_and_style(writer, all_rows_df, "All")

        # Per-environment tabs (if any)
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

        # Summary (if present as CSV)
        if summary_csv.exists():
            try:
                df_summary = pd.read_csv(summary_csv)
                df_summary.to_excel(writer, index=False, sheet_name="Summary")
                _autosize_and_style(writer, df_summary, "Summary")
            except Exception:
                pass

        # Baseline (if present) — itemized baseline.csv
        if baseline_csv.exists():
            try:
                df_base = pd.read_csv(baseline_csv)
                df_base.to_excel(writer, index=False, sheet_name="Baseline")
                _autosize_and_style(writer, df_base, "Baseline")
            except Exception:
                pass

        # Executive summary (always attempt)
        # try:
        #     exec_df = _build_exec_summary(all_rows_df)
        #     if not exec_df.empty:
        #         exec_df.to_excel(writer, index=False, sheet_name="ExecutiveSummary")
        #         _autosize_and_style(writer, exec_df, "ExecutiveSummary")
        # except Exception as e:
        #     print(f"⚠️ Executive summary skipped: {e}")
        # Executive summary (+ optional Baseline row)
        try:
            exec_df = _build_exec_summary(all_rows_df)
            baseline_total = 0.0
            if baseline_csv and baseline_csv.exists():
                try:
                    df_base = pd.read_csv(baseline_csv)
                    # Write Baseline sheet for transparency
                    df_base.to_excel(writer, index=False, sheet_name="Baseline")
                    _autosize_and_style(writer, df_base, "Baseline")
                    # Pull total from explicit TOTAL row if present, else sum monthly_usd
                    col = "monthly_usd"
                    if col in df_base.columns:
                        if "component" in df_base.columns and df_base["component"].astype(str).str.upper().eq("TOTAL").any():
                            baseline_total = float(pd.to_numeric(
                                df_base.loc[df_base["component"].astype(str).str.upper() == "TOTAL", col],
                                errors="coerce"
                            ).fillna(0.0).iloc[0])
                        else:
                            baseline_total = float(pd.to_numeric(df_base[col], errors="coerce").fillna(0.0).sum())
                except Exception:
                    baseline_total = 0.0

            # If we have a baseline total, append it to ExecutiveSummary and recompute Total row
            if baseline_total > 0 and not exec_df.empty:
                exec_wo_total = exec_df[exec_df["Item"] != "Total"].copy()
                base_row = pd.DataFrame([{
                    "Item": "AWS VPC Overhead (Baseline)",
                    "Per Unit Cost (mo)": "",
                    "Monthly Cost": round(baseline_total, 2),
                    "Annual Cost": round(baseline_total * 12.0, 2),
                }])
                exec_wo_total = pd.concat([exec_wo_total, base_row], ignore_index=True)
                new_total_monthly = float(exec_wo_total["Monthly Cost"].sum())
                total_row = pd.DataFrame([{
                    "Item": "Total",
                    "Per Unit Cost (mo)": "",
                    "Monthly Cost": round(new_total_monthly, 2),
                    "Annual Cost": round(new_total_monthly * 12.0, 2),
                }])
                exec_df = pd.concat([exec_wo_total, total_row], ignore_index=True)

            if not exec_df.empty:
                exec_df.to_excel(writer, index=False, sheet_name="ExecutiveSummary")
                _autosize_and_style(writer, exec_df, "ExecutiveSummary")
        except Exception as e:
            print(f"⚠️ Executive summary skipped: {e}")

    print(f"Wrote Excel workbook with environment tabs → {out_xlsx}")


# ---------------------- Executive summary ----------------------
def _build_exec_summary(df: pd.DataFrame) -> pd.DataFrame:
    """
    Itemized executive summary built from priced rows.

    - Compute items (Windows/RHEL/SUSE/Linux): sums monthly_compute_usd, per-unit is the average per row.
    - Database items (RDS/Azure SQL): sums monthly_db_usd, per-unit is the average per row.
    - Extra lines: Storage (block + object) and Network, summed once across all rows.
    - Adds Annual Cost and a Total row.
    """
    df = df.copy()

    # Normalize numeric columns we’ll aggregate
    for c in [
        "monthly_compute_usd", "monthly_ebs_usd", "monthly_s3_usd",
        "monthly_network_usd", "monthly_db_usd", "monthly_total_usd"
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        else:
            df[c] = 0.0

    # Label each row (compute vs RDS vs Azure SQL)
    os_col  = df.get("os", pd.Series([""] * len(df))).astype(str).str.strip().str.lower()
    engine  = df.get("db_engine", pd.Series([""] * len(df))).astype(str).str.strip().str.lower()
    cloud   = df.get("cloud", pd.Series([""] * len(df))).astype(str).str.strip().str.lower()
    has_az_sql = (cloud == "azure") & (df["monthly_db_usd"] > 0)

    labels = []
    for i in range(len(df)):
        row_os = os_col.iloc[i]
        if row_os == "windows":
            labels.append("Windows VMs")
        elif row_os == "rhel":
            labels.append("RHEL Servers")
        elif row_os == "suse":
            labels.append("SUSE Servers")
        elif row_os == "linux":
            labels.append("Linux VMs (generic)")
        elif has_az_sql.iloc[i]:
            # optional tier/deployment detail if present
            tier = str(df.get("az_sql_tier", pd.Series([""]*len(df))).iloc[i]).strip()
            dep  = str(df.get("az_sql_deployment", pd.Series([""]*len(df))).iloc[i]).strip()
            t = tier or "Azure SQL"
            d = f" – {dep}" if dep else ""
            labels.append(f"Azure SQL ({t}{d})")
        elif engine.iloc[i]:
            it = str(df.get("db_instance_class", pd.Series([""]*len(df))).iloc[i]).strip()
            itxt = f" ({it})" if it else ""
            labels.append(f"RDS {engine.iloc[i].capitalize()}{itxt}")
        else:
            labels.append("Other Compute/Services")

    df["_item_label"] = labels

    # Aggregate per label
    rows = []
    for label, sub in df.groupby("_item_label", dropna=False):
        if label.startswith(("RDS ", "Azure SQL")):
            unit = sub["monthly_db_usd"].mean()
            monthly = sub["monthly_db_usd"].sum()
        elif label.endswith(("VMs", "Servers")) or label.startswith("Linux"):
            unit = sub["monthly_compute_usd"].mean()
            monthly = sub["monthly_compute_usd"].sum()
        else:
            # Fallback – use total if we don’t recognize the bucket
            unit = sub["monthly_total_usd"].mean()
            monthly = sub["monthly_total_usd"].sum()

        rows.append({
            "Item": label,
            "Per Unit Cost (mo)": round(unit, 2) if unit > 0 else "",
            "Monthly Cost": round(monthly, 2),
        })

    # Add extra line items (singletons)
    def _add_extra(name: str, series: pd.Series):
        total = float(series.sum()) if series is not None else 0.0
        if total > 0:
            rows.append({"Item": name, "Per Unit Cost (mo)": "", "Monthly Cost": round(total, 2)})

    _add_extra("Block Storage (EBS/Managed Disk)", df.get("monthly_ebs_usd"))
    _add_extra("Object Storage (S3/Blob)",         df.get("monthly_s3_usd"))
    _add_extra("Network (egress/DTO)",             df.get("monthly_network_usd"))

    out = pd.DataFrame(rows)

    # Annual and totals
    if not out.empty:
        out["Annual Cost"] = (out["Monthly Cost"] * 12.0).round(2)
        total_row = pd.DataFrame([{
            "Item": "Total",
            "Per Unit Cost (mo)": "",
            "Monthly Cost": round(out["Monthly Cost"].sum(), 2),
            "Annual Cost": round((out["Monthly Cost"].sum() * 12.0), 2),
        }])
        out = pd.concat([out, total_row], ignore_index=True)

    return out


    # Add explicit storage/network rollups using dedicated columns (regardless of item labels)
    def _add_extra(label, col):
        if col in df.columns:
            val = float(df[col].sum())
            if val > 0:
                rows.append({"Item": label, "Per Unit Cost (mo)": "", "Monthly Cost": round(val, 2), "Annual Cost": round(val*12.0, 2)})

    _add_extra("Block Storage (EBS / Managed Disk)", "monthly_ebs_usd")
    _add_extra("Object Storage (S3 / Blob)", "monthly_s3_usd")
    _add_extra("Network Egress (DTO)", "monthly_network_usd")

    # Collapse to DataFrame and push Total to bottom
    out = pd.DataFrame(rows)
    if not out.empty:
        total_row = pd.DataFrame([{
            "Item": "Total",
            "Per Unit Cost (mo)": "",
            "Monthly Cost": round(out["Monthly Cost"].sum(), 2),
            "Annual Cost": round(out["Annual Cost"].sum(), 2),
        }])
        # Deduplicate any duplicate storage/network lines if grouping also produced them
        out = (out.groupby("Item", as_index=False)
                 .agg({"Per Unit Cost (mo)":"first","Monthly Cost":"sum","Annual Cost":"sum"}))
        # Move Total to bottom
        out = pd.concat([out[out["Item"]!="Total"], total_row], ignore_index=True)
    return out
# ---------------------- entry ----------------------
if __name__ == "__main__":
    cli()

