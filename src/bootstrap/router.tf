# pfSense CE ISO must be uploaded to Proxmox before apply:
#   Proxmox UI → Datacenter → local → ISO Images → Upload
#   Download CE from: https://www.pfsense.org/download/

resource "proxmox_virtual_environment_vm" "router001" {
  name        = "router001"
  description = "pfSense CE Router — WAN + cluster LAN"
  tags        = ["terraform", "pfsense", "router", "management"]
  node_name   = var.hypervisor_node
  on_boot     = true

  cpu {
    cores = 2
    type  = "x86-64-v2-AES"
  }

  memory {
    dedicated = 4096
  }

  agent {
    enabled = false   # pfSense: no qemu-guest-agent
  }

  # vtnet0 — WAN: site uplink
  network_device {
    bridge = var.wan_bridge
    model  = "virtio"
  }

  # vtnet1 — LAN: cluster data plane (pfSense is gateway here)
  network_device {
    bridge = var.cluster_bridge
    model  = "virtio"
  }

  disk {
    datastore_id = var.datastore_id
    file_format  = "raw"
    interface    = "virtio0"
    size         = 16
  }

  cdrom {
    file_id = var.pfsense_iso
  }

  operating_system {
    type = "other"   # FreeBSD
  }

  # No cloud-init: pfSense does not support it
  # First boot: complete installer interactively via Proxmox console, then eject ISO
}
