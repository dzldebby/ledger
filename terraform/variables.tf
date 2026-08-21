variable "aws_region" {
  description = "AWS region for all ledger infrastructure"
  type        = string
  default     = "us-east-1"
}

variable "db_bundle_id" {
  description = "Lightsail database bundle (pricing/size tier)"
  type        = string
  default     = "micro_2_0"
}

variable "container_power" {
  description = "Lightsail container service power (per-node pricing tier)"
  type        = string
  default     = "nano-1"
}

variable "container_scale" {
  description = "Number of container nodes (replicas) - satisfies HLD's 'two stateless instances'"
  type        = number
  default     = 2
}

variable "github_repo" {
  description = "GitHub repo in owner/name form, used to scope the OIDC trust policy"
  type        = string
  default     = "dzldebby/ledger"
}
