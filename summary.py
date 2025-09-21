# summary.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from glob import glob

import pandas as pd


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

