# recommender.py
from typing import Dict, List, Optional, Tuple

FAMILY_PREFS = {
    "balanced": ["m7i", "m6i", "m5"],
    "compute":  ["c7i", "c6i", "c5"],
    "memory":   ["r7i", "r6i", "r5"],
}

def infer_profile(vcpu: int, mem_gib: float) -> str:
    if vcpu <= 0 or mem_gib <= 0:
        return "balanced"
    mem_per_vcpu = mem_gib / vcpu
    if mem_per_vcpu <= 3.0:
        return "compute"
    if mem_per_vcpu >= 6.0:
        return "memory"
    return "balanced"

def fetch_instance_catalog(region: str) -> Dict[str, dict]:
    import boto3
    ec2 = boto3.client("ec2", region_name=region)
    paginator = ec2.get_paginator("describe_instance_types")
    page_it = paginator.paginate(Filters=[{"Name": "current-generation", "Values": ["true"]}])
    catalog = {}
    for page in page_it:
        for it in page.get("InstanceTypes", []):
            itype = it["InstanceType"]
            if itype.endswith(".metal"): continue
            archs = it.get("ProcessorInfo", {}).get("SupportedArchitectures", [])
            if "x86_64" not in archs: continue
            vcpu = it.get("VCpuInfo", {}).get("DefaultVCpus", 0)
            mem_mib = it.get("MemoryInfo", {}).get("SizeInMiB", 0)
            if vcpu <= 0 or mem_mib <= 0: continue
            catalog[itype] = {"instanceType": itype, "vcpu": vcpu, "memory_gib": mem_mib / 1024.0}
    return catalog

def _family_rank(families: List[str], itype: str) -> int:
    fam = itype.split(".")[0]
    try:
        return families.index(fam)
    except ValueError:
        return len(families) + 1

def pick_instance(catalog: Dict[str, dict], profile: str, need_vcpu: int, need_mem_gib: float) -> Optional[dict]:
    families = FAMILY_PREFS.get(profile, FAMILY_PREFS["balanced"])
    candidates = [t for t, info in catalog.items() if info["vcpu"] >= need_vcpu and info["memory_gib"] >= need_mem_gib]
    if not candidates: return None
    def sort_key(itype: str) -> Tuple[int, int, float, str]:
        info = catalog[itype]
        return (_family_rank(families, itype), info["vcpu"], info["memory_gib"], itype)
    candidates.sort(key=sort_key)
    return catalog[candidates[0]]

def smallest_meeting_cpu(catalog: Dict[str, dict], vcpu_needed: int) -> Optional[dict]:
    fits = [info for info in catalog.values() if info["vcpu"] >= vcpu_needed]
    if not fits: return None
    fits.sort(key=lambda x: (x["vcpu"], x["memory_gib"], x["instanceType"]))
    return fits[0]

def smallest_meeting_mem(catalog: Dict[str, dict], mem_needed: float) -> Optional[dict]:
    fits = [info for info in catalog.values() if info["memory_gib"] >= mem_needed]
    if not fits: return None
    fits.sort(key=lambda x: (x["memory_gib"], x["vcpu"], x["instanceType"]))
    return fits[0]
