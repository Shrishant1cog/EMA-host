from __future__ import annotations
from pathlib import Path
import re, sys
p = Path(sys.argv[1] if len(sys.argv) > 1 else '.env')
if not p.exists():
    print(f'ERROR: {p} not found')
    raise SystemExit(2)
bad=[]
for n,line in enumerate(p.read_text(encoding='utf-8', errors='replace').splitlines(),1):
    s=line.strip()
    if not s or s.startswith('#'): continue
    if re.match(r'^(export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=.*$', s): continue
    bad.append((n,s[:100]))
if bad:
    print(f'INVALID_ENV_LINES={len(bad)}')
    for n,s in bad: print(f'line {n}: {s}')
    raise SystemExit(1)
print(f'ENV_OK: {p}')
