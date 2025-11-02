from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import json
import os

try:
    import click  # only required for interactive prompt
except Exception:
    click = None  # type: ignore

# Reuse pricing constants/helpers for EC2, EBS, and S3
try:
    from pricing import (
        price_ec2_ondemand,
        EBS_GP3_GB_MONTH as _EBS_GP3_DEFAULT,
        S3_STD_GB_MONTH as _S3_STD_DEFAULT,
    )
except Exception:
    # Fallbacks if pricing module isn't available for any reason
    price_ec2_ondemand = None  # type: ignore
    _EBS_GP3_DEFAULT = 0.08
    _S3_STD_DEFAULT = 0.023


# --------------------------- Data & Defaults ---------------------------

# Built-in conservative defaults (USD)
# You can override via ENV or prices/aws_vpc_baseline.json
_DEFAULT_RATES = {
    "tgw_attachment_hourly": float(os.getenv("TGW_ATTACHMENT_HOURLY", "0.06")),  # $/attachment-hour
    "tgw_data_gb":           float(os.getenv("TGW_DATA_GB", "0.02")),            # $/GB
    "vpce_if_hourly":        float(os.getenv("VPCE_IF_HOURLY", "0.01")),         # $/endpoint-hour
    "vpce_data_gb":          float(os.getenv("VPCE_DATA_GB", "0.01")),           # $/GB
    # Added: storage pricing reused for GitRunner OS disk and TF backend S3
    "ebs_gp3_gb_month":      float(os.getenv("EBS_GP3_GB_MONTH", str(_EBS_GP3_DEFAULT))),  # $/GB-month
    "s3_std_gb_month":       float(os.getenv("S3_STD_GB_MONTH",  str(_S3_STD_DEFAULT))),   # $/GB-month
}

HOURS_DEFAULT = 730.0  # default hours/month across the app


@dataclass(frozen=True)
class BaselineInputs:
    region: str
    tgw_attachments: int
    tgw_data_gb: float
    vpce_base_per_az: int
    vpce_extra_per_az: int
    vpce_azs: int
    vpce_data_gb: float
    hours_per_month: float = HOURS_DEFAULT
    # Added: GitRunner EC2 + OS EBS and Terraform backend S3
    gitrunner_instance_type: str = "t3.medium"
    gitrunner_count: int = 1
    gitrunner_os_gb: float = 256.0
    tf_backend_s3_gb: float = 1.0


@dataclass(frozen=True)
class BaselineRates:
    tgw_attachment_hourly: float
    tgw_data_gb: float
    vpce_if_hourly: float
    vpce_data_gb: float
    ebs_gp3_gb_month: float
    s3_std_gb_month: float


# --------------------------- Rates & I/O ---------------------------

def _load_rates_from_json(region: str) -> Optional[Dict[str, float]]:
    """
    Optional per-region override JSON: prices/aws_vpc_baseline.json
    {
      "us-east-1": {
        "tgw_attachment_hourly": 0.06,
        "tgw_data_gb": 0.02,
        "vpce_if_hourly": 0.01,
        "vpce_data_gb": 0.01
      }
    }
    """
    p = Path("prices/aws_vpc_baseline.json")
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        region_rates = data.get(region) or data.get(region.strip().lower())
        if isinstance(region_rates, dict):
            return {k: float(v) for k, v in region_rates.items()}
        return None
    except Exception:
        return None


def resolve_rates(region: str) -> BaselineRates:
    # Priority: JSON override (per region) -> ENV -> built-ins
    merged = dict(_DEFAULT_RATES)
    j = _load_rates_from_json(region) or {}
    merged.update(j)
    return BaselineRates(
        tgw_attachment_hourly=float(merged["tgw_attachment_hourly"]),
        tgw_data_gb=float(merged["tgw_data_gb"]),
        vpce_if_hourly=float(merged["vpce_if_hourly"]),
        vpce_data_gb=float(merged["vpce_data_gb"]),
        ebs_gp3_gb_month=float(merged["ebs_gp3_gb_month"]),
        s3_std_gb_month=float(merged["s3_std_gb_month"]),
    )


# --------------------------- Computation ---------------------------

def compute_baseline(inputs: BaselineInputs, rates: BaselineRates) -> Tuple[List[dict], float]:
    """
    Returns (rows, total_monthly_usd).
    Rows are itemized for CSV/Excel.
    """
    hpmo = float(inputs.hours_per_month)

    # TGW
    tgw_attach_monthly = max(0, inputs.tgw_attachments) * rates.tgw_attachment_hourly * hpmo
    tgw_data_monthly = max(0.0, inputs.tgw_data_gb) * rates.tgw_data_gb

    # Interface Endpoints (PrivateLink) - per-AZ counts
    total_endpoints = (max(0, inputs.vpce_base_per_az) + max(0, inputs.vpce_extra_per_az)) * max(1, inputs.vpce_azs)
    vpce_attach_monthly = total_endpoints * rates.vpce_if_hourly * hpmo
    vpce_data_monthly = max(0.0, inputs.vpce_data_gb) * rates.vpce_data_gb

    rows = [
        {
            "component": "TGW Attachment",
            "detail": "attachments",
            "qty": inputs.tgw_attachments,
            "unit": "attachment-hour",
            "rate": f"{rates.tgw_attachment_hourly:.5f}",
            "monthly_usd": f"{tgw_attach_monthly:.2f}",
            "region": inputs.region,
            "notes": f"{hpmo:g} hours assumed",
        },
        {
            "component": "TGW Data",
            "detail": "data processed",
            "qty": inputs.tgw_data_gb,
            "unit": "GB",
            "rate": f"{rates.tgw_data_gb:.5f}",
            "monthly_usd": f"{tgw_data_monthly:.2f}",
            "region": inputs.region,
            "notes": "",
        },
        {
            "component": "Interface Endpoint",
            "detail": "endpoints x AZs",
            "qty": total_endpoints,
            "unit": "endpoint-hour",
            "rate": f"{rates.vpce_if_hourly:.5f}",
            "monthly_usd": f"{vpce_attach_monthly:.2f}",
            "region": inputs.region,
            "notes": f"{hpmo:g} hours assumed",
        },
        {
            "component": "Interface Endpoint Data",
            "detail": "data processed",
            "qty": inputs.vpce_data_gb,
            "unit": "GB",
            "rate": f"{rates.vpce_data_gb:.5f}",
            "monthly_usd": f"{vpce_data_monthly:.2f}",
            "region": inputs.region,
            "notes": "",
        },
    ]
    # GitRunner EC2 compute (On-Demand)
    if inputs.gitrunner_count > 0:
        # Prefer explicit env override to avoid requiring network/boto3 in many workflows
        gr_hourly_env = os.getenv("GITRUNNER_HOURLY")
        gr_hourly: float = 0.0
        if gr_hourly_env is not None:
            try:
                gr_hourly = float(gr_hourly_env)
            except Exception:
                gr_hourly = 0.0
        elif price_ec2_ondemand is not None:
            try:
                p = price_ec2_ondemand(inputs.gitrunner_instance_type, inputs.region, os_name="Linux")
                if p is not None:
                    gr_hourly = float(p)
            except BaseException:
                # Catch BaseException to handle SystemExit from missing boto3 path
                gr_hourly = 0.0

        gr_compute_monthly = float(inputs.gitrunner_count) * gr_hourly * hpmo
        rows.append({
            "component": "GitRunner EC2",
            "detail": f"{inputs.gitrunner_instance_type} x {inputs.gitrunner_count}",
            "qty": inputs.gitrunner_count,
            "unit": "instance-hour",
            "rate": f"{gr_hourly:.5f}",
            "monthly_usd": f"{gr_compute_monthly:.2f}",
            "region": inputs.region,
            "notes": f"{hpmo:g} hours assumed",
        })
        # GitRunner OS EBS (gp3 assumed)
        gr_ebs_monthly = float(inputs.gitrunner_count) * max(0.0, inputs.gitrunner_os_gb) * rates.ebs_gp3_gb_month
        rows.append({
            "component": "GitRunner EBS (OS)",
            "detail": f"gp3 {inputs.gitrunner_os_gb:g} GB x {inputs.gitrunner_count}",
            "qty": inputs.gitrunner_os_gb,
            "unit": "GB-month",
            "rate": f"{rates.ebs_gp3_gb_month:.5f}",
            "monthly_usd": f"{gr_ebs_monthly:.2f}",
            "region": inputs.region,
            "notes": "",
        })

    # Terraform backend S3 (Standard)
    if inputs.tf_backend_s3_gb > 0.0:
        tf_s3_monthly = max(0.0, inputs.tf_backend_s3_gb) * rates.s3_std_gb_month
        rows.append({
            "component": "Terraform Backend S3",
            "detail": "Standard storage",
            "qty": inputs.tf_backend_s3_gb,
            "unit": "GB-month",
            "rate": f"{rates.s3_std_gb_month:.5f}",
            "monthly_usd": f"{tf_s3_monthly:.2f}",
            "region": inputs.region,
            "notes": "",
        })
    # Sum all rows we've added so far
    running_total = tgw_attach_monthly + tgw_data_monthly + vpce_attach_monthly + vpce_data_monthly
    # Add any GitRunner/S3 rows we just appended
    try:
        running_total += gr_compute_monthly  # type: ignore[name-defined]
    except Exception:
        pass
    try:
        running_total += gr_ebs_monthly  # type: ignore[name-defined]
    except Exception:
        pass
    try:
        running_total += tf_s3_monthly  # type: ignore[name-defined]
    except Exception:
        pass
    total = running_total
    rows.append({
        "component": "TOTAL",
        "detail": "",
        "qty": "",
        "unit": "",
        "rate": "",
        "monthly_usd": f"{total:.2f}",
        "region": inputs.region,
        "notes": "",
    })
    return rows, total


# --------------------------- Writer ---------------------------

def write_baseline_csv(run_dir: Path, rows: List[dict]) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "baseline.csv"
    import csv
    fieldnames = ["component", "detail", "qty", "unit", "rate", "monthly_usd", "region", "notes"]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return out


# --------------------------- Prompt Flow ---------------------------

def prompt_for_inputs() -> BaselineInputs:
    """
    Interactive prompts with your requested defaults:
      - tgw_attachments = 1
      - tgw_data_gb = 100
      - Number of Environments? (new) → default 1
      - vpce_base_per_az default = 8 × environments (core services per AZ)
    We also default:
      - vpce_extra_per_az = 0
      - vpce_azs = 2
      - vpce_data_gb = (copy of tgw_data_gb unless overridden)
    """
    if click is None:
        raise RuntimeError("Interactive prompts require the 'click' package. Please install click or construct BaselineInputs programmatically.")
    region = click.prompt("AWS region (e.g., us-east-1)", type=str).strip()

    tgw_attachments = click.prompt("Number of TGW attachments", type=int, default=1)
    tgw_data_gb = click.prompt("Estimated total TGW data processed per month (GB)", type=float, default=100.0)

    # Environments-driven base interface endpoints per AZ (no confirmation prompt)
    num_envs = click.prompt("Number of Environments?", type=int, default=1)
    base_ep = 8 * max(1, int(num_envs))
    click.echo(f"Base Interface Endpoints per AZ (core services) set to {base_ep} (8 × {max(1, int(num_envs))}).")
    vpce_base_per_az = base_ep
    vpce_extra_per_az = click.prompt("Extra Interface Endpoints per AZ (e.g., RDS, Backup)", type=int, default=0)
    vpce_azs = click.prompt("Number of AZs", type=int, default=2)

    vpce_data_gb_default = tgw_data_gb
    vpce_data_gb = click.prompt("Total data processed by all Interface Endpoints per month (GB)",
                                type=float, default=vpce_data_gb_default)

    # GitRunner (EC2) defaults
    gitrunner_instance_type = click.prompt("GitRunner EC2 instance type", type=str, default="t3.medium").strip()
    gitrunner_count = click.prompt("Number of GitRunner instances", type=int, default=1)
    gitrunner_os_gb = click.prompt("GitRunner OS EBS size (GB)", type=float, default=256.0)

    # Terraform backend S3 (Standard)
    tf_backend_s3_gb = click.prompt("Terraform backend S3 storage (GB)", type=float, default=1.0)

    return BaselineInputs(
        region=region,
        tgw_attachments=max(0, tgw_attachments),
        tgw_data_gb=max(0.0, tgw_data_gb),
        vpce_base_per_az=max(0, vpce_base_per_az),
        vpce_extra_per_az=max(0, vpce_extra_per_az),
        vpce_azs=max(1, vpce_azs),
        vpce_data_gb=max(0.0, vpce_data_gb),
        hours_per_month=HOURS_DEFAULT,
        gitrunner_instance_type=gitrunner_instance_type or "t3.medium",
        gitrunner_count=max(0, gitrunner_count),
        gitrunner_os_gb=max(0.0, gitrunner_os_gb),
        tf_backend_s3_gb=max(0.0, tf_backend_s3_gb),
    )
