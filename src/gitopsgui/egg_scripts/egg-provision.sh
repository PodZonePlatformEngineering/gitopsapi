#!/usr/bin/env bash
set -euo pipefail
# Required env vars:
#   CLUSTER_NAME      e.g. "mercury-management"
#   VIP               e.g. "192.168.4.150"
#   TEMPLATE_VMID     e.g. "9000"
#   NEW_VMID          e.g. "100"
#   STORAGE           e.g. "zfs-pool-01"
#   BRIDGE            e.g. "vmbr0"
#   CPU               e.g. "4"
#   MEMORY_MB         e.g. "8192"
#   DISK_GB           e.g. "50"
#   TALOS_VERSION     e.g. "v1.12.6"
#   TALOS_SCHEMA_ID
#   K8S_VERSION       e.g. "v1.34.6"
#   INSTALL_DISK      e.g. "/dev/vda"

KUBECONFIG_PATH="/tmp/${CLUSTER_NAME}.kubeconfig"

# Install talosctl if not present
if ! command -v talosctl &>/dev/null; then
  echo "Installing talosctl..." >&2
  curl -sL "https://github.com/siderolabs/talos/releases/download/${TALOS_VERSION}/talosctl-linux-amd64" \
    -o /usr/local/bin/talosctl && chmod +x /usr/local/bin/talosctl
fi

# Idempotency: skip VM creation if already exists
if ! qm list | awk 'NR>1{print $1}' | grep -qx "$NEW_VMID"; then
  qm clone "$TEMPLATE_VMID" "$NEW_VMID" --name "$CLUSTER_NAME" --full
  qm set "$NEW_VMID" \
    --cores "$CPU" \
    --memory "$MEMORY_MB" \
    --net0 "virtio,bridge=${BRIDGE}"
  qm resize "$NEW_VMID" scsi0 "${DISK_GB}G"
fi

# Generate Talos config
TALOS_DIR="/tmp/${CLUSTER_NAME}-talos"
mkdir -p "$TALOS_DIR"

if [ ! -f "$TALOS_DIR/controlplane.yaml" ]; then
  talosctl gen config "$CLUSTER_NAME" "https://${VIP}:6443" \
    --output-dir "$TALOS_DIR" \
    --with-docs=false \
    --config-patch '[{"op":"add","path":"/machine/install/disk","value":"'"$INSTALL_DISK"'"}]' \
    --config-patch '[{"op":"add","path":"/cluster/allowSchedulingOnControlPlanes","value":true}]'
fi

# Start VM and apply config
qm start "$NEW_VMID" 2>/dev/null || true

# Wait for Talos API (port 50000)
echo "Waiting for Talos API on VM..." >&2
VM_IP=""
for i in $(seq 1 60); do
  VM_IP=$(qm guest cmd "$NEW_VMID" network-get-interfaces 2>/dev/null \
    | python3 -c "
import json,sys
d=json.load(sys.stdin)
for iface in d:
    for addr in iface.get('ip-addresses',[]):
        ip=addr.get('ip-address','')
        if ip.startswith('192.168.') and not ip.endswith('.1'):
            print(ip); exit()
" 2>/dev/null || true)
  [ -n "$VM_IP" ] && break
  sleep 5
done

talosctl apply-config --insecure --nodes "$VM_IP" \
  --file "$TALOS_DIR/controlplane.yaml"

# Bootstrap etcd
sleep 10
talosctl --talosconfig "$TALOS_DIR/talosconfig" bootstrap --nodes "$VM_IP"

# Retrieve kubeconfig
talosctl --talosconfig "$TALOS_DIR/talosconfig" kubeconfig \
  --nodes "$VM_IP" "$KUBECONFIG_PATH"

echo "{\"status\":\"provisioned\",\"cluster\":\"$CLUSTER_NAME\",\"vip\":\"$VIP\",\"kubeconfig_path\":\"$KUBECONFIG_PATH\"}"
