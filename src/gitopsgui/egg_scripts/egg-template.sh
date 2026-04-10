#!/usr/bin/env bash
set -euo pipefail
# Required env vars:
#   TALOS_VERSION   e.g. "v1.12.6"
#   TALOS_SCHEMA_ID e.g. "6adc7e7fba27948460e2231e5272e88b85159da3f3db980551976bf9898ff64b"
#   VMID            e.g. "9000"
#   STORAGE         e.g. "zfs-pool-01"
#   BRIDGE          e.g. "vmbr0"

TEMPLATE_NAME="talos-${TALOS_VERSION}"

# Idempotency: exit if template already exists
if qm list | awk 'NR>1{print $2}' | grep -qx "$TEMPLATE_NAME"; then
  echo "{\"status\":\"skipped\",\"reason\":\"template $TEMPLATE_NAME already exists\",\"vmid\":$VMID}"
  exit 0
fi

IMAGE_URL="https://factory.talos.dev/image/${TALOS_SCHEMA_ID}/${TALOS_VERSION}/nocloud-amd64.raw.xz"
IMAGE_XZ="/tmp/${TEMPLATE_NAME}.raw.xz"
IMAGE_RAW="/tmp/${TEMPLATE_NAME}.raw"

echo "Downloading Talos image ${TALOS_VERSION}..." >&2
wget -q -O "$IMAGE_XZ" "$IMAGE_URL"
xz -d "$IMAGE_XZ"

qm create "$VMID" \
  --name "$TEMPLATE_NAME" \
  --memory 2048 --cores 2 \
  --net0 "virtio,bridge=${BRIDGE}" \
  --ostype l26 --agent enabled=1

qm importdisk "$VMID" "$IMAGE_RAW" "$STORAGE"
qm set "$VMID" --scsihw virtio-scsi-pci --scsi0 "${STORAGE}:vm-${VMID}-disk-0"
qm set "$VMID" --boot c --bootdisk scsi0
qm template "$VMID"

rm -f "$IMAGE_RAW"

echo "{\"status\":\"created\",\"template\":\"$TEMPLATE_NAME\",\"vmid\":$VMID}"
