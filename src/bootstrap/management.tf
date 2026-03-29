resource "proxmox_virtual_environment_vm" "management-cp01" {
  name        = "management-cp01"
  description = "Management Cluster Control Plane"
  tags        = ["terraform", "talos", "controlplane", "management"]
  node_name   = var.hypervisor_node
  on_boot     = true

  cpu {
    cores = 4
    type  = "x86-64-v2-AES"
  }

  memory {
    dedicated = 8192
  }

  agent {
    enabled = true
  }

  network_device {
    bridge = var.management_bridge   # vmbr0 only — not pfSense
  }

  disk {
    datastore_id = "local"
    file_id      = proxmox_virtual_environment_download_file.talos_nocloud_image.id
    file_format  = "raw"
    interface    = "virtio0"
    size         = 20
  }

  operating_system {
    type = "l26"
  }

  initialization {
    datastore_id = var.pool_datastore_id
    ip_config {
      ipv4 {
        address = "${var.management_cp_ip}/24"
        gateway = var.default_gateway
      }
      ipv6 {
        address = "dhcp"
      }
    }
  }
}
