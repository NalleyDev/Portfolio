variable "vsphere_user" {
  description = "vSphere username"
  type        = string
}

variable "vsphere_password" {
  description = "vSphere password"
  type        = string
  sensitive   = true
}

variable "vsphere_server" {
  description = "vSphere server address"
  type        = string
}

variable "datacenter" {
  description = "The name of the vSphere datacenter"
  type        = string
}

variable "datastore" {
  description = "The name of the vSphere datastore"
  type        = string
}

variable "network" {
  description = "The name of the vSphere network"
  type        = string
}

variable "cluster" {
  description = "The name of the vSphere cluster"
  type        = string
}

variable "control_plane_count" {
  description = "Number of control plane nodes"
  type        = number
  default     = 3
}

variable "worker_count" {
  description = "Number of worker nodes"
  type        = number
  default     = 3
}

variable "num_cpus" {
  description = "Number of vCPUs per node"
  type        = number
  default     = 2
}

variable "memory" {
  description = "Amount of memory in MB per node"
  type        = number
  default     = 4096
}

variable "disk_size" {
  description = "Disk size in GB per node"
  type        = number
  default     = 20
}

variable "domain" {
  description = "Domain name for the nodes"
  type        = string
  default     = "localdomain"
}

variable "template" {
  description = "VM template for AlmaLinux 9"
  type        = string
  default     = "alma-linux-9-template"
}

variable "node_network_prefix" {
  description = "First three octets of the node network, e.g. 10.0.10"
  type        = string
  default     = "192.168.1"
}

variable "node_gateway" {
  description = "Default gateway for the node network"
  type        = string
  default     = "192.168.1.1"
}

variable "control_plane_ip_start" {
  description = "Last octet of the first control plane node"
  type        = number
  default     = 50
}

variable "worker_ip_start" {
  description = "Last octet of the first worker node"
  type        = number
  default     = 100
}
