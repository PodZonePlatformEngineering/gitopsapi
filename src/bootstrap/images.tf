locals {
  talos = {
    version      = "v1.9.5"
    # Vanilla schematic — no extensions, no extraManifests
    # Verify/regenerate at: https://factory.talos.dev (select nothing)
    schematic_id = "376567988ad370138ad8b2698212367b8edcb69b47e37947f2630b7fd0a80c4"
  }
}

resource "proxmox_virtual_environment_download_file" "talos_nocloud_image" {
  content_type = "iso"
  datastore_id = "local"
  node_name    = var.hypervisor_node
  file_name    = "talos-${local.talos.version}-nocloud-amd64-management.img"
  url          = "https://factory.talos.dev/image/${local.talos.schematic_id}/${local.talos.version}/nocloud-amd64.iso"
  overwrite    = false
}
