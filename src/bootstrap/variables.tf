# Proxmox
variable "proxmox_endpoint"   { description = "Proxmox API URL e.g. https://192.168.4.50:8006" }
variable "proxmox_username"   { description = "Proxmox user e.g. root@pam" }
variable "proxmox_password"   { sensitive = true }
variable "hypervisor_node"    { default = "venus" }
variable "datastore_id"       { default = "local" }
variable "pool_datastore_id"  { default = "pool1" }

# Network bridges — platform attributes, vary per site
variable "default_gateway"    { description = "Site LAN gateway e.g. 192.168.4.1" }
variable "management_bridge"  { description = "Control plane bridge — not connected to pfSense. e.g. vmbr0" }
variable "cluster_bridge"     { description = "Cluster data plane bridge — pfSense LAN side. e.g. vmbr1" }
variable "wan_bridge"         { description = "WAN/uplink bridge — pfSense WAN side. e.g. vmbr2" }

# NIC names — confirm with: ip link show on hypervisor
variable "cluster_lan_nic"    { description = "Physical NIC for cluster_bridge e.g. enp3s0f0" }
variable "wan_nic"            { description = "Physical NIC for wan_bridge e.g. enp3s0f1" }
variable "storage_nic"        { description = "Physical NIC for storage_bridge (vmbr3) e.g. eno2" }

# Network bridges (names)
variable "storage_bridge"     { description = "Storage replication bridge e.g. vmbr3"; default = "vmbr3" }

# Management cluster
variable "management_cp_ip"   { description = "Management CP static IP e.g. 192.168.4.211" }

# pfSense
variable "pfsense_lan_ip"     { description = "pfSense LAN IP on cluster_bridge e.g. 10.1.0.1" }
variable "pfsense_iso"        {
  description = "Proxmox ISO reference e.g. local:iso/pfSense-CE-2.7.2-RELEASE-amd64.iso"
}
