variable "region" {
  description = "AWS region (data residency: Mumbai)."
  type        = string
  default     = "ap-south-1"
}

variable "env" {
  description = "Single pilot environment; staging optional via workspace."
  type        = string
  default     = "pilot"
}

variable "instance_type" {
  description = "Graviton box per doc 05 §2; fallback t3.xlarge if ARM wheels missing."
  type        = string
  default     = "t4g.xlarge"
}

variable "ami_id" {
  description = "ARM64 Ubuntu AMI for ap-south-1. Overridden per-apply; placeholder validates."
  type        = string
  default     = "ami-0000000000000000"
}

variable "root_volume_gb" {
  type    = number
  default = 30
}

variable "data_volume_gb" {
  type    = number
  default = 100
}

variable "domain_name" {
  description = "Route53 hosted-zone domain; empty disables DNS records."
  type        = string
  default     = ""
}

variable "alarm_email" {
  description = "SNS subscription target for CloudWatch alarms."
  type        = string
  default     = "ops@example.com"
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "public_subnet_cidr" {
  type    = string
  default = "10.20.1.0/24"
}
