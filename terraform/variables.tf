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
  default     = "nano"
}

variable "container_scale" {
  description = "Number of container nodes (replicas). HLD calls for 2 (redundant, stateless); running 1 here to cut cost for a demo/learning deployment - bump to 2 with a single `terraform apply` when actually needed."
  type        = number
  default     = 1
}

variable "github_repo" {
  description = "GitHub repo in owner/name form, used to scope the OIDC trust policy"
  type        = string
  default     = "dzldebby/ledger"
}

variable "github_repo_immutable" {
  description = <<-EOT
    Immutable form of github_repo, carrying @<numeric-id> suffixes on both the
    owner and repo slugs. GitHub now issues OIDC subject claims in this form so
    they survive renames, and it does NOT match the legacy owner/name pattern.
    Find the value in the CloudTrail AssumeRoleWithWebIdentity event:
      aws cloudtrail lookup-events \
        --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity
  EOT
  type        = string
  default     = "dzldebby@19401055/ledger@1330920517"
}
