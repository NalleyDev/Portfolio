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
  description = "vCenter FQDN or IP"
  type        = string
}

variable "panos_host" {
  description = "PAN-OS management FQDN or IP"
  type        = string
}

variable "panos_user" {
  description = "PAN-OS username"
  type        = string
}

variable "panos_password" {
  description = "PAN-OS password"
  type        = string
  sensitive   = true
}

variable "datacenter" {
  description = "vSphere datacenter name"
  type        = string
}

variable "datastore" {
  description = "vSphere datastore name"
  type        = string
}

variable "network" {
  description = "vSphere port group name"
  type        = string
}

variable "template" {
  description = "VM template to clone"
  type        = string
}

variable "domain" {
  description = "DNS domain for guest customization"
  type        = string
  default     = "example.com"
}

variable "num_cpus" {
  description = "vCPUs for the firewall VM"
  type        = number
  default     = 2
}

variable "memory" {
  description = "Memory in MB for the firewall VM"
  type        = number
  default     = 4096
}

variable "disk_size" {
  description = "Disk size in GB"
  type        = number
  default     = 60
}

variable "fw_ip_address" {
  description = "Management IP for the firewall VM"
  type        = string
  default     = "192.168.2.1"
}

variable "fw_gateway" {
  description = "Default gateway for the firewall VM"
  type        = string
  default     = "192.168.2.254"
}

variable "trust_cidr" {
  description = "CIDR treated as internal by the Allow-Internal-Traffic rule"
  type        = string
  default     = "192.168.2.0/24"
}
