terraform {
  required_providers {
    panos = {
      source  = "PaloAltoNetworks/panos"
      version = "~> 1.11.1"
    }
    vsphere = {
      source  = "hashicorp/vsphere"
      version = "~> 2.0"
    }
  }
}

provider "vsphere" {
  user           = var.vsphere_user
  password       = var.vsphere_password
  vsphere_server = var.vsphere_server
  allow_unverified_ssl = true
}

provider "panos" {
  hostname = var.panos_host
  username = var.panos_user
  password = var.panos_password
}

data "vsphere_datacenter" "dc" {
  name = var.datacenter
}

data "vsphere_datastore" "datastore" {
  name          = var.datastore
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_network" "network" {
  name          = var.network
  datacenter_id = data.vsphere_datacenter.dc.id
}

resource "vsphere_virtual_machine" "firewall_vm" {
  name             = "panos-fw"
  resource_pool_id = data.vsphere_datacenter.dc.id
  datastore_id     = data.vsphere_datastore.datastore.id

  num_cpus = var.num_cpus
  memory   = var.memory
  guest_id = "other3xLinux64Guest"

  network_interface {
    network_id   = data.vsphere_network.network.id
    adapter_type = "vmxnet3"
  }

  disk {
    label            = "disk0"
    size             = var.disk_size
    eagerly_scrub    = false
    thin_provisioned = true
  }

  clone {
    template_uuid = data.vsphere_virtual_machine.template.id

    customize {
      linux_options {
        host_name = "panos-fw"
        domain    = var.domain
      }

      network_interface {
        ipv4_address = var.fw_ip_address
        ipv4_netmask = 24
      }

      ipv4_gateway = var.fw_gateway
    }
  }
}

# PAN-OS Security Rule
resource "panos_security_rule" "allow_internal" {
  name                  = "Allow-Internal-Traffic"
  description           = "Allow all internal traffic"
  rule_type             = "universal"
  source_zones          = ["trust"]
  source_addresses      = [var.trust_cidr]
  destination_zones     = ["trust"]
  destination_addresses = [var.trust_cidr]
  applications          = ["any"]
  action               = "allow"
  log_setting          = "default"
}
