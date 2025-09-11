# summary.py
from pathlib import Path
import pandas as pd

def write_run_summary(run_dir: Path, recommend_path: Path | None, price_path: Path | None) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "summary.csv"
    rows = []

    if recommend_path and recommend_path.exists():
        df = (pd.read_excel(recommend_path) if recommend_path.suffix.lower() in {".xlsx",".xls"} else
              pd.read_csv(recommend_path))
        for c in ("overprov_vcpu","overprov_mem_gib"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        rows.append(pd.DataFrame({
            "metric": ["rows_recommended","avg_overprov_vcpu","avg_overprov_mem_gib"],
            "value": [len(df), df.get("overprov_vcpu",0).mean(skipna=True), df.get("overprov_mem_gib",0).mean(skipna=True)]
        }))

    if price_path and price_path.exists():
        dfp = (pd.read_excel(price_path) if price_path.suffix.lower() in {".xlsx",".xls"} else
               pd.read_csv(price_path))
        for c in ["monthly_compute_usd","monthly_ebs_usd","monthly_s3_usd","monthly_network_usd","monthly_db_usd","monthly_total_usd"]:
            if c in dfp.columns:
                dfp[c] = pd.to_numeric(dfp[c], errors="coerce")
        rows.append(pd.DataFrame({
            "metric": ["monthly_compute_total","monthly_ebs_total","monthly_s3_total","monthly_network_total","monthly_db_total","monthly_grand_total"],
            "value": [dfp.get("monthly_compute_usd",0).sum(skipna=True),
                      dfp.get("monthly_ebs_usd",0).sum(skipna=True),
                      dfp.get("monthly_s3_usd",0).sum(skipna=True),
                      dfp.get("monthly_network_usd",0).sum(skipna=True),
                      dfp.get("monthly_db_usd",0).sum(skipna=True),
                      dfp.get("monthly_total_usd",0).sum(skipna=True)]
        }))

    out_df = (pd.concat(rows, ignore_index=True) if rows else pd.DataFrame({"metric":[],"value":[]}))
    out_df.to_csv(out, index=False)
    return out
