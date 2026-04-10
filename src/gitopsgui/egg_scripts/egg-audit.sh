#!/usr/bin/env bash
set -euo pipefail

# Discover bridges
BRIDGES=$(pvesh get /nodes/$(hostname)/network --output-format json 2>/dev/null \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(json.dumps([i['iface'] for i in d if i.get('type') == 'bridge']))
" 2>/dev/null || echo '[]')

# Discover storage pools
STORAGE_POOLS=$(pvesm status --output-format json 2>/dev/null \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(json.dumps([i['storage'] for i in d if i.get('active',0)==1]))
" 2>/dev/null || echo '[]')

# Discover template VMs (name only, e.g. "talos-v1.12.6")
TEMPLATE_VMS=$(qm list 2>/dev/null \
  | awk 'NR>1' \
  | while read vmid name rest; do
      if qm config "$vmid" 2>/dev/null | grep -q "^template: 1"; then
        echo "$name"
      fi
    done \
  | python3 -c "import sys,json; lines=[l.strip() for l in sys.stdin if l.strip()]; print(json.dumps(lines))" \
  || echo '[]')

# Discover Proxmox cluster nodes
PROXMOX_NODES=$(pvesh get /nodes --output-format json 2>/dev/null \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(json.dumps([i['node'] for i in d]))
" 2>/dev/null || echo '["'"$(hostname)"'"]')

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

python3 - <<EOF
import json
print(json.dumps({
    "bridges": $BRIDGES,
    "storage_pools": $STORAGE_POOLS,
    "template_vms": $TEMPLATE_VMS,
    "proxmox_nodes": $PROXMOX_NODES,
    "last_audited": "$TIMESTAMP"
}, indent=2))
EOF
