# vmbr0 already exists on each hypervisor — import if Terraform needs to manage it, do not recreate
# venus:  backed by eno3 (enp1s0f2)
# saturn: backed by enp68s0f0 — MIGRATE to eno1 before apply (see notes below)
#
# saturn vmbr0 migration (do before terraform apply on saturn):
#   1. Cable saturn eno1 → LAN switch
#   2. In Proxmox UI: edit vmbr0, change bridge port from enp68s0f0 → eno1
#   3. This frees enp68s0f0 for vmbr3 (storage, DAC to venus eno2)
#
# import { to = proxmox_virtual_environment_network_linux_bridge.management; id = "venus:vmbr0" }

resource "proxmox_virtual_environment_network_linux_bridge" "cluster_lan" {
  node_name = var.hypervisor_node
  name      = var.cluster_bridge   # vmbr1
  comment   = "Cluster data plane — pfSense LAN side; DAC to peer hypervisor"
  ports     = [var.cluster_lan_nic]
}

resource "proxmox_virtual_environment_network_linux_bridge" "wan" {
  node_name = var.hypervisor_node
  name      = var.wan_bridge       # vmbr2
  comment   = "WAN / site uplink — pfSense WAN side"
  ports     = [var.wan_nic]
}

resource "proxmox_virtual_environment_network_linux_bridge" "storage" {
  node_name = var.hypervisor_node
  name      = var.storage_bridge   # vmbr3
  comment   = "Storage replication — Linstor/Piraeus + iSCSI/NFS; DAC to peer hypervisor"
  ports     = [var.storage_nic]
}
