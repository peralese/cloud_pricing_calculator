# recommender.py
from typing import Dict, List, Optional, Tuple
from pathlib import Path
import json, os, sys

# ---------- AWS sizing ----------
FAMILY_PREFS = {
    "balanced": ["m7i", "m6i", "m5"],
    "compute":  ["c7i", "c6i", "c5"],
    "memory":   ["r7i", "r6i", "r5"],
}

_AZ_REGION_NORMALIZE = {
    "east us": "eastus", "eastus": "eastus",
    "east us 2": "eastus2", "eastus2": "eastus2",
    "west us": "westus", "westus": "westus",
    "west us 2": "westus2", "westus2": "westus2",
}

def normalize_azure_region(s: str) -> str:
    key = " ".join(str(s or "").lower().split())
    return _AZ_REGION_NORMALIZE.get(key, key.replace(" ", ""))


def infer_profile(vcpu: int, mem_gib: float) -> str:
    if vcpu <= 0 or mem_gib <= 0:
        return "balanced"
    mem_per_vcpu = mem_gib / vcpu
    if mem_per_vcpu <= 3.0: return "compute"
    if mem_per_vcpu >= 6.0: return "memory"
    return "balanced"

def _lazy_boto3():
    try:
        import boto3  # type: ignore
        return boto3
    except ImportError:
        print("This feature requires boto3. Install with: pip install boto3", file=sys.stderr)
        sys.exit(1)

def fetch_instance_catalog(region: str) -> Dict[str, dict]:
    boto3 = _lazy_boto3()
    ec2 = boto3.client("ec2", region_name=region)
    paginator = ec2.get_paginator("describe_instance_types")
    page_it = paginator.paginate(Filters=[{"Name":"current-generation","Values":["true"]}])

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
            catalog[itype] = {"instanceType": itype, "vcpu": vcpu, "memory_gib": mem_mib/1024.0}
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
    def sort_key(itype: str) -> Tuple[int,int,float,str]:
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

# ---------- Azure sizing (dynamic via SDK/CLI with cache) ----------
def _azure_list_vm_sizes_via_sdk(region: str):
    try:
        from azure.identity import DefaultAzureCredential  # type: ignore
        from azure.mgmt.compute import ComputeManagementClient  # type: ignore
    except Exception:
        return None
    try:
        cred = DefaultAzureCredential(exclude_interactive_browser_credential=False)
        sub = os.environ.get("AZURE_SUBSCRIPTION_ID")
        if not sub:
            return None
        client = ComputeManagementClient(cred, sub)
        sizes = list(client.virtual_machine_sizes.list(location=region))
        return [{"name": s.name, "vcpu": int(s.number_of_cores), "memory_gib": float(s.memory_in_mb)/1024.0} for s in sizes]
    except Exception:
        return None

def _azure_list_vm_sizes_via_cli(region: str):
    import subprocess, json as _json, shutil, sys
    az = shutil.which("az") or shutil.which("az.cmd") or shutil.which("az.exe")
    if not az:
        return None
    try:
        out = subprocess.check_output([az, "vm", "list-sizes", "--location", region, "-o", "json"], stderr=subprocess.STDOUT)
        sizes = _json.loads(out.decode("utf-8"))
        return [{"name": s["name"], "vcpu": int(s["numberOfCores"]), "memory_gib": float(s["memoryInMb"]) / 1024.0} for s in sizes]
    except subprocess.CalledProcessError as e:
        msg = e.output.decode("utf-8", errors="ignore") if getattr(e, "output", None) else str(e)
        print(f"Azure CLI call failed for region '{region}'. Ensure `az login` and subscription are set.\n{msg}", file=sys.stderr)
        return None
    except Exception:
        return None


def _azure_catalog_cache_path(region: str) -> Path:
    Path("cache").mkdir(exist_ok=True)
    return Path(f"cache/azure_vm_sizes_{region}.json")

def _azure_load_cached_sizes(region: str):
    p = _azure_catalog_cache_path(region)
    if p.exists():
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def _azure_save_cached_sizes(region: str, sizes: List[dict]):
    try:
        with open(_azure_catalog_cache_path(region), "w", encoding="utf-8") as f:
            json.dump(sizes, f, indent=2)
    except Exception:
        pass

def fetch_azure_vm_catalog(region: str) -> Dict[str, dict]:
    sizes = _azure_list_vm_sizes_via_sdk(region) or _azure_list_vm_sizes_via_cli(region) or _azure_load_cached_sizes(region)
    if not sizes:
        raise SystemExit(
            f"Azure VM sizes unavailable for region '{region}'. "
            "Install 'azure-identity azure-mgmt-compute' or Azure CLI and login, or provide a cached file under ./cache."
        )
    # cache if fetched fresh
    if sizes and not _azure_load_cached_sizes(region):
        _azure_save_cached_sizes(region, sizes)
    catalog = { s["name"]: {"instanceType": s["name"], "vcpu": s["vcpu"], "memory_gib": s["memory_gib"]} for s in sizes }
    return catalog

def pick_azure_size(catalog: Dict[str, dict], need_vcpu: int, need_mem_gib: float) -> Optional[dict]:
    candidates = [info for info in catalog.values() if info["vcpu"] >= need_vcpu and info["memory_gib"] >= need_mem_gib]
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x["vcpu"], x["memory_gib"], x["instanceType"]))
    return candidates[0]

