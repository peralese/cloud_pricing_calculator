import os, time, pathlib
p = pathlib.Path("prices/azure_compute_cache_eastus.json")
old = time.time() - 30*24*3600
os.utime(p, (old, old))
print("Backdated cache mtime ->", time.ctime(p.stat().st_mtime))