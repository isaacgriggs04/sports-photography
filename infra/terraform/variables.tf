variable "project_name" {
  type    = string
  default = "sports-photography"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "aws_region" {
  type    = string
  default = "us-east-2"
}

variable "vpc_id" {
  type        = string
  description = "VPC ID where networked resources should be created (for example, vpc-abc123)."
}

variable "subnet_ids" {
  type        = list(string)
  description = "Subnet IDs in the target VPC for ECS tasks, RDS subnet group, and GPU worker."
  validation {
    condition     = length(var.subnet_ids) > 0
    error_message = "subnet_ids must contain at least one subnet ID."
  }
}

variable "uploads_bucket_name" {
  type        = string
  default     = ""
  description = "Existing S3 bucket name for uploads. If set, Terraform will reuse it."
}

variable "thumbnails_bucket_name" {
  type        = string
  default     = ""
  description = "Existing S3 bucket name for thumbnails. If empty and uploads_bucket_name is set, uploads bucket is reused."
}

variable "db_name" {
  type    = string
  default = "sportspic"
}

variable "db_username" {
  type    = string
  default = "sportspic"
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "api_image_tag" {
  type    = string
  default = "latest"
}

variable "worker_image_tag" {
  type    = string
  default = "latest"
}

variable "api_container_port" {
  type    = number
  default = 8080
}

variable "frontend_origin" {
  type    = string
  default = "http://localhost:5173"
}

variable "clerk_secret_key" {
  type      = string
  sensitive = true
}

variable "stripe_secret_key" {
  type      = string
  sensitive = true
}

variable "worker_shared_secret" {
  type      = string
  sensitive = true
}

variable "create_gpu_worker_instance" {
  type    = bool
  default = true
}

variable "gpu_instance_type" {
  type    = string
  default = "g4dn.xlarge"
}

variable "public_key" {
  type        = string
  default     = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAOjFuC9eJ9afNaD9EavnojdXRDqFoJSIros9pmqcBpo"
  description = "Optional SSH public key for GPU EC2 instance"
}
