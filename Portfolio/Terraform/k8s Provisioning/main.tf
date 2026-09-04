provider "vsphere" {
  user           = var.vsphere_user
  password       = var.vsphere_password
  vsphere_server = var.vsphere_server

  allow_unverified_ssl = true
}

resource "vsphere_compute_cluster" "k8s_cluster" {
  name          = "k8s-cluster"
  datacenter_id = data.vsphere_datacenter.dc.id
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

data "vsphere_compute_cluster" "cluster" {
  name          = var.cluster
  datacenter_id = data.vsphere_datacenter.dc.id
}

resource "vsphere_virtual_machine" "control_plane_nodes" {
  count = var.control_plane_count

  name             = "k8s-cp-${count.index}"
  resource_pool_id = data.vsphere_compute_cluster.cluster.resource_pool_id
  datastore_id     = data.vsphere_datastore.datastore.id

  num_cpus = var.num_cpus
  memory   = var.memory
  guest_id = "rhel9_64Guest"

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
        host_name = "k8s-cp-${count.index}"
        domain    = var.domain
      }

      network_interface {
        ipv4_address = "${var.node_network_prefix}.${var.control_plane_ip_start + count.index}"
        ipv4_netmask = 24
      }

      ipv4_gateway = var.node_gateway
    }
  }
}

resource "vsphere_virtual_machine" "worker_nodes" {
  count = var.worker_count

  name             = "k8s-worker-${count.index}"
  resource_pool_id = data.vsphere_compute_cluster.cluster.resource_pool_id
  datastore_id     = data.vsphere_datastore.datastore.id

  num_cpus = var.num_cpus
  memory   = var.memory
  guest_id = "rhel9_64Guest"

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
        host_name = "k8s-worker-${count.index}"
        domain    = var.domain
      }

      network_interface {
        ipv4_address = "${var.node_network_prefix}.${var.worker_ip_start + count.index}"
        ipv4_netmask = 24
      }

      ipv4_gateway = var.node_gateway
    }
  }
}

data "vsphere_virtual_machine" "template" {
  name          = var.template
  datacenter_id = data.vsphere_datacenter.dc.id
}
