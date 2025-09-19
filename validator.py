# validator.py
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import re

# --- Canonical region sets (keep these reasonably up-to-date) ---

# AWS public regions (non-Gov/China)
AWS_REGIONS = {
    "us-east-1","us-east-2","us-west-1","us-west-2",
    "ca-central-1",
    "eu-west-1","eu-west-2","eu-west-3","eu-north-1","eu-south-1","eu-central-1","eu-central-2",
    "ap-south-1","ap-south-2","ap-southeast-1","ap-southeast-2","ap-southeast-3","ap-northeast-1","ap-northeast-2","ap-northeast-3",
    "ap-east-1",
    "sa-east-1",
    "af-south-1",
    "me-south-1","me-central-1"
}

# ✅ AWS GovCloud regions
AWS_GOV_REGIONS = {
    "us-gov-west-1",
    "us-gov-east-1",
}

# Union of commercial + GovCloud (used by normalization & membership checks)
ALL_AWS_REGIONS = AWS_REGIONS | AWS_GOV_REGIONS

# Azure canonical slugs (match what your recommender supports)
AZURE_REGIONS = {
    "eastus","eastus2","westus","westus2","westus3","centralus","northcentralus","southcentralus",
    "northeurope","westeurope","eastasia","southeastasia","japaneast","japanwest","australiaeast",
    "australiasoutheast","australiacentral","brazilsouth","southindia","centralindia","westindia",
    "canadacentral","canadaeast","westcentralus","uksouth","ukwest","koreacentral","koreasouth",
    "francecentral","southafricanorth","uaenorth","switzerlandnorth","germanywestcentral","norwayeast",
    "jioindiawest","westus3","swedencentral","qatarcentral","polandcentral","italynorth","israelcentral",
    "spaincentral","mexicocentral","malaysiawest","newzealandnorth","indonesiacentral","austriaeast","chilecentral"
}


# ---- Tier A (required to size/recommend) ----
TIER_A_ENUMS = {
    "cloud": {"aws", "azure"},
}
TIER_A_REQUIRED = ["cloud", "region"]
TIER_A_AT_LEAST_ONE = ["vcpu", "memory_gib"]  # at least one must be present and > 0 if provided

# ---- Tier B (required to price accurately) ----
TIER_B_ENUMS = {
    "os": {"linux", "windows"},
    "purchase_option": {"ondemand", "spot", "reserved"},
    "profile": {"balanced", "compute", "memory"},
    "arch": {"x86", "arm"},
}
TIER_B_REQUIRED_FOR_PRICING = ["os", "purchase_option", "root_gb", "root_type"]

# Cloud-specific toggles (validate if present)
CLOUD_SPECIFIC_BOOL = {
    "aws": ["byol"],
    "azure": ["ahub"],
}

_NULLISH_STR = {"", "nan", "null", "none", "n/a", "#n/a"}

@dataclass
class Issue:
    level: str          # "error" | "warn"
    field: str
    reason: str
    fix_hint: str

@dataclass
class ValidationResult:
    status: str         # "ok" | "rec_only" | "error"
    blocking_for: str   # "none" | "pricing" | "recommendation"
    issues: List[Issue]

def _is_missing(v) -> bool:
    # None or empty string
    if v is None:
        return True
    # Pandas/NumPy NaN
    try:
        import math
        if isinstance(v, float) and math.isnan(v):
            return True
    except Exception:
        pass
    try:
        import pandas as pd  # type: ignore
        if pd.isna(v):  # handles NaN, NaT
            return True
    except Exception:
        pass
    # Stringy nulls
    s = str(v).strip().lower()
    return s in _NULLISH_STR

def _norm_str(v) -> Optional[str]:
    """Return a normalized non-empty string or None if missing/nullish."""
    if _is_missing(v):
        return None
    s = str(v).strip()
    # Treat "nan", "null", etc. as missing
    if s.lower() in _NULLISH_STR:
        return None
    return s

def _to_float_pos(v) -> Optional[float]:
    """Parse a positive float (>0). Return None for missing/invalid/non-positive."""
    if _is_missing(v):
        return None
    try:
        val = float(v)
        return val if val > 0 else None
    except Exception:
        return None

def _is_bool_like(v: str) -> bool:
    return str(v).strip().lower() in {"true", "false", "0", "1", "yes", "no"}

def _closest_choices(token: str, choices: List[str], n: int = 5) -> List[str]:
    try:
        import difflib
        return difflib.get_close_matches(token, choices, n=n, cutoff=0.0)
    except Exception:
        return choices[:n]
    
# ---- AWS region normalization (GovCloud-aware) ----
_AWS_REGION_ALIASES: Dict[str, str] = {
    "aws govcloud us-west": "us-gov-west-1",
    "aws-gov-west": "us-gov-west-1",
    "govcloud-us-west": "us-gov-west-1",
    "gov-west-1": "us-gov-west-1",
    "aws govcloud us-east": "us-gov-east-1",
    "aws-gov-east": "us-gov-east-1",
    "govcloud-us-east": "us-gov-east-1",
    "gov-east-1": "us-gov-east-1",
}

def _normalize_aws_region(r: str) -> str:
    """
    Normalize human-ish AWS region inputs to canonical codes.
    Returns the input lowercased if no mapping found.
    """
    k = str(r or "").strip().lower()
    if k in ALL_AWS_REGIONS:
        return k
    if k in _AWS_REGION_ALIASES:
        return _AWS_REGION_ALIASES[k]
    k2 = k.replace("govcloud-us", "us-gov")  # 'govcloud-us-west-1' -> 'us-gov-west-1'
    return k2

def _validate_region_for_cloud(cloud: Optional[str], region_raw: Optional[str]) -> List[Issue]:
    issues: List[Issue] = []
    c = (cloud or "").strip().lower()
    r = _norm_str(region_raw)
    if r is None:
        # missing region is handled by Tier A presence check; nothing to add here
        return issues

    r_l = r.lower()

    if c == "azure":
        # try to normalize via recommender if available
        try:
            from recommender import normalize_azure_region  # local import to avoid hard dep at import-time
            norm = normalize_azure_region(r_l)
        except Exception:
            norm = r_l

        if norm not in AZURE_REGIONS:
            suggestions = ", ".join(_closest_choices(r_l, sorted(AZURE_REGIONS)))
            issues.append(Issue(
                "error", "region",
                f"invalid Azure region '{region_raw}'",
                f"Use canonical Azure slugs (e.g., eastus, eastus2). Closest: {suggestions}"
            ))
        elif norm != r_l:
            # region looked like an alias; allow but warn
            issues.append(Issue(
                "warn", "region",
                f"normalized '{region_raw}' to '{norm}'",
                "Prefer canonical Azure slugs (e.g., eastus)."
            ))

    elif c == "aws":
        # GovCloud-aware normalization (accept aliases, warn if normalized)
        norm = _normalize_aws_region(r_l)
        if norm in ALL_AWS_REGIONS:
            if norm != r_l:
                issues.append(Issue(
                    "warn", "region",
                    f"normalized '{region_raw}' to '{norm}'",
                    "Prefer canonical AWS codes (e.g., us-gov-west-1, us-east-1)."
                ))
        else:
            choices = sorted(list(ALL_AWS_REGIONS))
            suggestions = ", ".join(_closest_choices(r_l, choices))
            # helpful hint if they passed Azure-style
            if r_l in AZURE_REGIONS:
                fix = f"'{region_raw}' looks like an Azure region. Use AWS codes like us-east-1, us-west-2, or us-gov-west-1. Closest: {suggestions}"
            else:
                fix = f"Use AWS region codes like us-east-1, us-west-2, or us-gov-west-1. Closest: {suggestions}"
            issues.append(Issue(
                "error", "region",
                f"invalid AWS region '{region_raw}'",
                fix
            ))
    else:
        # unknown cloud already handled elsewhere
        pass

    return issues

def validate_row(row: Dict) -> ValidationResult:
    issues: List[Issue] = []

    # ---------- Tier A: recommendation gate ----------
    # presence
    for f in TIER_A_REQUIRED:
        if _is_missing(row.get(f)):
            issues.append(Issue("error", f, "missing", f"Provide {f}."))

    # enums
    cloud = _norm_str(row.get("cloud"))
    if cloud and cloud.lower() not in TIER_A_ENUMS["cloud"]:
        issues.append(Issue("error", "cloud", f"invalid '{row.get('cloud')}'", "Use one of: aws, azure"))
    
    issues.extend(_validate_region_for_cloud(cloud, row.get("region")))
    if any(i.level == "error" and i.field == "region" for i in issues):
        return ValidationResult(status="error", blocking_for="recommendation", issues=issues)

    # capacity: at least one of vcpu / memory_gib present and > 0
    vcpu_pos = _to_float_pos(row.get("vcpu"))
    mem_pos  = _to_float_pos(row.get("memory_gib"))
    if vcpu_pos is None and mem_pos is None:
        issues.append(Issue("error", "vcpu|memory_gib", "both missing or non-positive",
                            "Provide vcpu (>0), memory_gib (>0), or both."))

    # early exit if recommendation is blocked
    if any(i.level == "error" for i in issues):
        return ValidationResult(status="error", blocking_for="recommendation", issues=issues)

    # ---------- Tier B: pricing gate ----------
    # enums (warn if invalid, but don't block recommendation)
    for f, allowed in TIER_B_ENUMS.items():
        v = _norm_str(row.get(f))
        if v is not None and v.lower() not in allowed:
            issues.append(Issue("warn", f, f"invalid '{v}'", f"Use one of: {sorted(allowed)}"))

    # required for pricing
    missing_b = [f for f in TIER_B_REQUIRED_FOR_PRICING if _is_missing(row.get(f))]

    # cloud-specific bools (optional but validate if present)
    cloud_l = cloud.lower() if cloud else ""
    for f in CLOUD_SPECIFIC_BOOL.get(cloud_l, []):
        v = row.get(f)
        if not _is_missing(v) and not _is_bool_like(v):
            issues.append(Issue("warn", f, f"non-boolean '{v}'", "Use true/false."))

    if missing_b:
        for f in missing_b:
            issues.append(Issue("warn", f, "missing for pricing", f"Provide {f} to enable pricing."))
        return ValidationResult(status="rec_only", blocking_for="pricing", issues=issues)

    # all good for pricing
    return ValidationResult(status="ok", blocking_for="none", issues=issues)

def validate_dataframe(df, input_file: str) -> Tuple[List[int], List[int], List[int], List[Dict]]:
    """
    Returns (ok_idx, rec_only_idx, error_idx, report_rows)
    report_rows: list of dicts for validator_report.csv
    """
    ok_idx, rec_only_idx, error_idx = [], [], []
    report = []
    for i, row in df.iterrows():
        res = validate_row(row.to_dict())
        if res.status == "ok":
            ok_idx.append(i)
        elif res.status == "rec_only":
            rec_only_idx.append(i)
        else:
            error_idx.append(i)
        report.append({
            "row_index": i,
            "input_file": input_file,
            "status": res.status,
            "blocking_for": res.blocking_for,
            "reasons": "; ".join(f"{it.level}:{it.field}:{it.reason}" for it in res.issues),
            "fix_hints": " | ".join(sorted({it.fix_hint for it in res.issues}))
        })
    return ok_idx, rec_only_idx, error_idx, report

def write_validator_report(report_rows: List[Dict], out_csv_path: str) -> None:
    """Write the validator report to CSV."""
    import csv
    from pathlib import Path

    Path(out_csv_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["row_index", "input_file", "status", "blocking_for", "reasons", "fix_hints"]
        )
        writer.writeheader()
        writer.writerows(report_rows)


