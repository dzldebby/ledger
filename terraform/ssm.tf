resource "aws_ssm_parameter" "database_url" {
  name  = "/ledger/database_url"
  type  = "SecureString"
  value = local.database_url
}
