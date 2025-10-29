# summary.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from glob import glob

import pandas as pd
import time

try:
    import click  # For interactive prompts
except Exception:
    click = None  # type: ignore


@dataclass
class RunArtifacts:
    run_dir: Path
    recommend_path: Optional[Path]
    price_path: Optional[Path]
    validator_report_path: Optional[Path]


def _find_first(path: Path, names: List[str]) -> Optional[Path]:
    for n in names:
        p = path / n
        if p.exists():
            return p
    return None


def _coerce_number(x) -> float:
    try:
        if x is None or (isinstance(x, str) and x.strip() == ""):
            return 0.0
        return float(x)
    except Exception:
        return 0.0


def _load_table(p: Optional[Path]) -> Optional[pd.DataFrame]:
    if not p:
        return None
    suf = p.suffix.lower()
    if suf == ".csv":
        return pd.read_csv(p)
    if suf in {".xlsx", ".xls"}:
        # For our outputs: default first sheet is "Results"
        return pd.read_excel(p, sheet_name=0)
    return None

def _find_baseline_csv_prefer(run_dir: Path) -> Optional[Path]:
    p = run_dir / "baseline.csv"
    if p.exists():
        return p
    cands = glob("output/**/baseline.csv", recursive=True)
    if not cands:
        return None
    return max((Path(c) for c in cands), key=lambda q: q.stat().st_mtime)

def _sum_baseline_from(csv_path: Optional[Path]) -> float:
    if not csv_path or not csv_path.exists():
        return 0.0
    try:
        df = pd.read_csv(csv_path)
        if "monthly_usd" not in df.columns:
            return 0.0
        if "component" in df.columns and df["component"].astype(str).str.upper().eq("TOTAL").any():
            return float(pd.to_numeric(
                df.loc[df["component"].astype(str).str.upper() == "TOTAL", "monthly_usd"],
                errors="coerce"
            ).fillna(0.0).iloc[0])
        return float(pd.to_numeric(df["monthly_usd"], errors="coerce").fillna(0.0).sum())
    except Exception:
        return 0.0

def _sum_baseline(run_dir: Path) -> float:
    """If baseline.csv exists in run_dir, return explicit TOTAL row if present, else sum monthly_usd."""
    p = run_dir / "baseline.csv"
    if not p.exists():
        return 0.0
    try:
        df = pd.read_csv(p)
        if "monthly_usd" not in df.columns:
            return 0.0
        # Prefer explicit TOTAL row
        if "component" in df.columns:
            tot = df[df["component"].astype(str).str.upper() == "TOTAL"]
            if not tot.empty:
                return float(pd.to_numeric(tot["monthly_usd"], errors="coerce").fillna(0.0).iloc[0])
        return float(pd.to_numeric(df["monthly_usd"], errors="coerce").fillna(0.0).sum())
    except Exception:
        return 0.0


def _detect_artifacts(run_dir: Path, rec_path: Optional[Path], price_path: Optional[Path]) -> RunArtifacts:
    # Allow function to infer sibling files from run_dir if not provided
    rec = rec_path if rec_path and Path(rec_path).exists() else _find_first(run_dir, ["recommend.csv", "recommend.xlsx", "recommend.xls"])
    price = price_path if price_path and Path(price_path).exists() else _find_first(run_dir, ["price.csv", "price.xlsx", "price.xls"])
    validator = _find_first(run_dir, ["validator_report.csv", "validator_report.xlsx"])
    return RunArtifacts(run_dir=run_dir, recommend_path=rec, price_path=price, validator_report_path=validator)


def _summarize_validator(df: pd.DataFrame) -> Dict[str, Any]:
    # Expect columns from validator report; be tolerant of missing
    status_col = None
    for c in df.columns:
        if str(c).strip().lower() in {"status", "row_status", "rowstatus"}:
            status_col = c
            break
    counts = {}
    if status_col:
        vc = df[status_col].fillna("").astype(str).str.lower().value_counts()
        for k, v in vc.items():
            counts[f"rows_{k}"] = int(v)
    total_rows = int(len(df))
    return {"validator_rows": total_rows, **counts}


def _summarize_recommend(df: pd.DataFrame) -> Dict[str, Any]:
    # Key columns (best-effort)
    out: Dict[str, Any] = {"recommend_rows": int(len(df))}
    # Averages on requested resources
    for col in ("requested_vcpu", "vcpu"):
        if col in df.columns:
            out["avg_requested_vcpu"] = round(float(df[col].astype("float64").mean()), 2)
            break
    for col in ("requested_memory_gib", "memory_gib"):
        if col in df.columns:
            out["avg_requested_memory_gib"] = round(float(df[col].astype("float64").mean()), 2)
            break
    # Fit reason counts
    if "fit_reason" in df.columns:
        vc = df["fit_reason"].fillna("").astype(str).str.lower().value_counts()
        for k, v in vc.items():
            if k:
                out[f"fit_{k}"] = int(v)
    return out


def _summarize_price(df: pd.DataFrame) -> Tuple[Dict[str, Any], pd.DataFrame]:
    # Numeric monthly columns
    monthly_cols = [
        "monthly_compute_usd",
        "monthly_ebs_usd",
        "monthly_s3_usd",
        "monthly_network_usd",
        "monthly_db_usd",
        "monthly_total_usd",
    ]
    out: Dict[str, Any] = {"priced_rows": int(len(df))}
    # Coerce numeric
    for c in monthly_cols + ["price_per_hour_usd"]:
        if c in df.columns:
            df[c] = df[c].apply(_coerce_number)
        else:
            df[c] = 0.0

    # Sums/averages
    out["sum_monthly_compute_usd"] = round(float(df["monthly_compute_usd"].sum()), 2)
    out["sum_monthly_ebs_usd"] = round(float(df["monthly_ebs_usd"].sum()), 2)
    out["sum_monthly_s3_usd"] = round(float(df["monthly_s3_usd"].sum()), 2)
    out["sum_monthly_network_usd"] = round(float(df["monthly_network_usd"].sum()), 2)
    out["sum_monthly_db_usd"] = round(float(df["monthly_db_usd"].sum()), 2)
    out["sum_monthly_total_usd"] = round(float(df["monthly_total_usd"].sum()), 2)

    out["avg_price_per_hour_usd"] = round(float(df["price_per_hour_usd"].replace(0.0, pd.NA).dropna().mean() or 0.0), 4)
    out["avg_monthly_total_usd"] = round(float(df["monthly_total_usd"].replace(0.0, pd.NA).dropna().mean() or 0.0), 2)

    # Top N by monthly total
    top = df.copy()
    top = top.sort_values("monthly_total_usd", ascending=False).head(5)
    # keep a friendly view
    keep_cols = [c for c in [
        "id", "region", "recommended_instance_type", "price_per_hour_usd", "monthly_total_usd"
    ] if c in top.columns]
    top_view = top[keep_cols] if keep_cols else top
    return out, top_view.reset_index(drop=True)


def _write_summary_files(run_dir: Path, summary_kv: Dict[str, Any], top_df: Optional[pd.DataFrame]) -> None:
    # summary.csv (two-column: metric,value)
    rows = [{"metric": k, "value": v} for k, v in summary_kv.items()]
    out_csv = run_dir / "summary.csv"
    pd.DataFrame(rows, columns=["metric", "value"]).to_csv(out_csv, index=False, encoding="utf-8")
    # summary.json
    out_json = run_dir / "summary.json"
    out_json.write_text(json.dumps(summary_kv, indent=2), encoding="utf-8")
    # optional topN
    if top_df is not None and len(top_df) > 0:
        top_df.to_csv(run_dir / "summary_top5.csv", index=False, encoding="utf-8")


def _append_summary_sheet_to_workbook(workbook_path: Path, summary_kv: Dict[str, Any], top_df: Optional[pd.DataFrame]) -> None:
    # Append/replace a sheet named "Summary" if workbook is .xlsx/.xls
    if workbook_path.suffix.lower() not in {".xlsx", ".xls"}:
        return
    # Build small frames
    kv_df = pd.DataFrame([{"Metric": k, "Value": v} for k, v in summary_kv.items()])
    with pd.ExcelWriter(workbook_path, mode="a", engine="openpyxl", if_sheet_exists="replace") as writer:
        kv_df.to_excel(writer, index=False, sheet_name="Summary")
        if top_df is not None and len(top_df) > 0:
            # Put Top 5 on a second sheet for clarity
            top_df.to_excel(writer, index=False, sheet_name="Top 5 (Monthly)")


def write_run_summary(run_dir: Path, recommend_path: Optional[Path], price_path: Optional[Path]) -> None:
    """
    Generate per-run summary artifacts:
      - summary.csv      (metric,value)
      - summary.json     (machine-readable)
      - summary_top5.csv (optional)
      - Append/replace 'Summary' sheet into any .xlsx in the run folder (recommend/price)
    This function is best-effort and should never raise.
    """
    try:
        arts = _detect_artifacts(run_dir, recommend_path, price_path)

        rec_df = _load_table(arts.recommend_path)
        price_df = _load_table(arts.price_path)
        val_df = _load_table(arts.validator_report_path)

        summary_kv: Dict[str, Any] = {}

        if val_df is not None:
            summary_kv.update(_summarize_validator(val_df))
        if rec_df is not None:
            summary_kv.update(_summarize_recommend(rec_df))

        top_df: Optional[pd.DataFrame] = None
        if price_df is not None:
            price_stats, top_df = _summarize_price(price_df)
            summary_kv.update(price_stats)

        # Baseline roll up (if any)
        baseline_total = _sum_baseline(arts.run_dir)
        if baseline_total > 0.0:
            # Add explicit metrics and an all-in grand total if we already have monthly sum
            summary_kv["monthly_baseline_total"] = round(baseline_total, 2)
            if "sum_monthly_total_usd" in summary_kv:
                summary_kv["monthly_grand_total_including_baseline"] = round(
                    float(summary_kv["sum_monthly_total_usd"]) + baseline_total, 2
                )

        # Write files
        _write_summary_files(arts.run_dir, summary_kv, top_df)

        # Append Summary sheet into any xlsx produced
        for wb in [arts.recommend_path, arts.price_path]:
            if wb and wb.exists() and wb.suffix.lower() in {".xlsx", ".xls"}:
                _append_summary_sheet_to_workbook(wb, summary_kv, top_df)
    except Exception:
        # Silent best-effort; callers shouldn't fail a run due to summary issues
        return


# ---------------------- Global tracking (interactive) ----------------------
def _load_tracking_df(path: Path, sheet: str = "Tracking") -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=[
            "Application Name", "ESATS ID", "ECS #",
            "Linux VMs", "Windows VMs",
            "Monthly Baseline USD",
            "Monthly Compute+Storage+Network+DB USD",
            "Monthly Grand Total USD",
            "Annualized Grand Total USD",
            "Run Folder",
        ])
    try:
        return pd.read_excel(path, sheet_name=sheet)
    except Exception:
        # Workbook exists but sheet missing → return empty with columns
        return pd.DataFrame(columns=[
            "Application Name", "ESATS ID", "ECS #",
            "Linux VMs", "Windows VMs",
            "Monthly Baseline USD",
            "Monthly Compute+Storage+Network+DB USD",
            "Monthly Grand Total USD",
            "Annualized Grand Total USD",
            "Run Folder",
        ])


def _write_tracking_df_with_retry(path: Path, df: pd.DataFrame, sheet: str = "Tracking", retries: int = 5, delay_s: float = 0.5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    last_err: Optional[Exception] = None
    for _ in range(max(1, retries)):
        try:
            with pd.ExcelWriter(path, engine="openpyxl", mode=("a" if path.exists() else "w"), if_sheet_exists="replace") as writer:
                df.to_excel(writer, index=False, sheet_name=sheet)
            return
        except Exception as e:
            # In case the file is temporarily locked by Excel/another process
            last_err = e
            time.sleep(delay_s)
    if last_err:
        raise last_err


def _count_os(price_df: Optional[pd.DataFrame]) -> Tuple[int, int]:
    if price_df is None or price_df.empty:
        return 0, 0
    ser = price_df.get("os")
    if ser is None:
        # No explicit OS column; best-effort: assume Linux if unstated
        total = int(len(price_df))
        return total, 0
    s = ser.fillna("").astype(str).str.strip().str.lower()
    win = int((s == "windows").sum())
    # Treat Linux flavors as Linux
    lin = int(((s == "linux") | (s == "rhel") | (s == "suse")).sum())
    # Unknowns: count them as Linux to match pricing default behavior
    unk = int((~((s == "windows") | (s == "linux") | (s == "rhel") | (s == "suse"))).sum())
    return lin + unk, win


def _compute_run_rollups(run_dir: Path) -> Dict[str, float | int]:
    arts = _detect_artifacts(run_dir, None, None)
    price_df = _load_table(arts.price_path)

    # Roll-ups from priced rows
    linux_count, windows_count = _count_os(price_df)

    if price_df is None:
        price_total = 0.0
    else:
        # Use the same coercion as summary
        col = "monthly_total_usd"
        if col in price_df.columns:
            price_df[col] = pd.to_numeric(price_df[col], errors="coerce").fillna(0.0)
            price_total = float(price_df[col].sum())
        else:
            price_total = 0.0

    baseline_total = _sum_baseline(run_dir)
    grand_total = price_total + baseline_total
    annual_total = grand_total * 12.0

    return {
        "linux_vms": int(linux_count),
        "windows_vms": int(windows_count),
        "monthly_baseline_usd": round(baseline_total, 2),
        "monthly_all_services_usd": round(price_total, 2),
        "monthly_grand_total_usd": round(grand_total, 2),
        "annualized_grand_total_usd": round(annual_total, 2),
    }


def prompt_and_update_tracking(run_dir: Path) -> None:
    """Interactively prompt to add current run results to output/tracking.xlsx.

    Asks user to confirm, then collects Application Name, ESATS ID, and ECS #,
    auto-populates costs and counts from the run's priced output and baseline.
    Idempotent update by Application Name; offers overwrite when name exists.
    """
    try:
        if click is None:
            return  # Non-interactive environment; skip silently

        proceed = click.prompt("Add results to global tracking sheet? (y/n)", type=str, default="n").strip().lower()
        if proceed not in {"y", "yes"}:
            return

        app_name = click.prompt("What is the Application Name?", type=str).strip()
        if not app_name:
            click.echo("Skipping: Application Name is required.")
            return

        tracking_path = Path("output") / "tracking.xlsx"
        sheet = "Tracking"
        df = _load_tracking_df(tracking_path, sheet)

        # Case-insensitive match on Application Name
        name_col = "Application Name"
        existing_idx = None
        if not df.empty and name_col in df.columns:
            mask = df[name_col].fillna("").astype(str).str.strip().str.lower() == app_name.lower()
            if mask.any():
                existing_idx = int(mask[mask].index[0])
                ow = click.prompt("App already exists — overwrite entry? (y/n)", type=str, default="n").strip().lower()
                if ow not in {"y", "yes"}:
                    click.echo("Keeping existing entry. No changes made.")
                    return

        esats_id = click.prompt("What is the ESATS ID?", type=str, default="").strip()
        ecs_no = click.prompt("What is the ECS #?", type=str, default="").strip()

        # Compute metrics from this run
        roll = _compute_run_rollups(run_dir)

        row = {
            "Application Name": app_name,
            "ESATS ID": esats_id,
            "ECS #": ecs_no,
            "Linux VMs": roll["linux_vms"],
            "Windows VMs": roll["windows_vms"],
            "Monthly Baseline USD": roll["monthly_baseline_usd"],
            "Monthly Compute+Storage+Network+DB USD": roll["monthly_all_services_usd"],
            "Monthly Grand Total USD": roll["monthly_grand_total_usd"],
            "Annualized Grand Total USD": roll["annualized_grand_total_usd"],
            "Run Folder": str(run_dir),
        }

        if df.empty:
            out_df = pd.DataFrame([row])
        else:
            if name_col not in df.columns:
                df[name_col] = ""
            if existing_idx is not None and existing_idx in df.index:
                for k, v in row.items():
                    if k not in df.columns:
                        df[k] = None
                    df.at[existing_idx, k] = v
                out_df = df
            else:
                # Append
                for k in row.keys():
                    if k not in df.columns:
                        df[k] = None
                out_df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

        _write_tracking_df_with_retry(tracking_path, out_df, sheet)
        click.echo(f"Updated tracking workbook → {tracking_path}")
    except Exception as e:
        # Best-effort; never crash a pricing run over tracking issues
        try:
            if click is not None:
                click.echo(f"⚠️ Tracking update skipped: {e}")
        except Exception:
            pass

