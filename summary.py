# summary.py
from pathlib import Path
import pandas as pd

def _read_any(p: Path) -> pd.DataFrame:
    if p.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(p)
    return pd.read_csv(p)

def write_run_summary(run_dir: Path, recommend_path: Path | None, price_path: Path | None) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "summary.csv"
    blocks: list[pd.DataFrame] = []

    # Recommend summary
    if recommend_path and recommend_path.exists():
        dfr = _read_any(recommend_path).copy()
        for c in ("overprov_vcpu","overprov_mem_gib"):
            if c in dfr.columns:
                dfr[c] = pd.to_numeric(dfr[c], errors="coerce")
        blocks.append(pd.DataFrame({
            "metric": ["rows_recommended","avg_overprov_vcpu","avg_overprov_mem_gib"],
            "value": [
                len(dfr),
                dfr.get("overprov_vcpu", pd.Series(dtype=float)).mean(skipna=True),
                dfr.get("overprov_mem_gib", pd.Series(dtype=float)).mean(skipna=True),
            ]
        }))

    # Price summary
    if price_path and price_path.exists():
        dfp = _read_any(price_path).copy()
        for c in ["monthly_compute_usd","monthly_ebs_usd","monthly_s3_usd","monthly_network_usd","monthly_db_usd","monthly_total_usd"]:
            if c in dfp.columns:
                dfp[c] = pd.to_numeric(dfp[c], errors="coerce")
        blocks.append(pd.DataFrame({
            "metric": ["monthly_compute_total","monthly_ebs_total","monthly_s3_total","monthly_network_total","monthly_db_total","monthly_grand_total"],
            "value": [
                dfp.get("monthly_compute_usd",0).sum(skipna=True),
                dfp.get("monthly_ebs_usd",0).sum(skipna=True),
                dfp.get("monthly_s3_usd",0).sum(skipna=True),
                dfp.get("monthly_network_usd",0).sum(skipna=True),
                dfp.get("monthly_db_usd",0).sum(skipna=True),
                dfp.get("monthly_total_usd",0).sum(skipna=True),
            ]
        }))

    out_df = (pd.concat(blocks, ignore_index=True) if blocks else pd.DataFrame({"metric":[],"value":[]}))
    out_df.to_csv(out, index=False)
    return out
