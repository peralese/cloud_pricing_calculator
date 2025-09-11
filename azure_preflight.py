# azure_preflight.py
import shutil, subprocess

class AzurePreflightError(Exception): pass

def ensure_azure_ready():
    az = shutil.which("az") or shutil.which("az.cmd") or shutil.which("az.exe")
    if not az:
        raise AzurePreflightError("Azure CLI not found. Install https://aka.ms/azcli and run 'az login'.")

    p = subprocess.run([az, "account", "show", "-o", "table"], capture_output=True, text=True)
    if p.returncode != 0:
        raise AzurePreflightError("Azure CLI not logged in. Run 'az login' and 'az account set --subscription <SUBSCRIPTION>'.")

    p = subprocess.run([az, "provider", "show", "-n", "Microsoft.Compute", "--query", "registrationState", "-o", "tsv"],
                       capture_output=True, text=True)
    if p.returncode != 0 or p.stdout.strip().lower() != "registered":
        raise AzurePreflightError("Provider Microsoft.Compute not registered. Run: az provider register -n Microsoft.Compute")
