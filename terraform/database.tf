resource "random_password" "db_master" {
  length           = 24
  special          = true
  override_special = "!#$%^&*()-_=+[]{}<>:?"
}

resource "aws_lightsail_database" "ledger" {
  relational_database_name = "ledger-db"
  blueprint_id              = "postgres_16"
  bundle_id                 = var.db_bundle_id

  master_database_name = "ledger"
  master_username       = "ledger"
  master_password        = random_password.db_master.result

  publicly_accessible       = false
  backup_retention_enabled  = true
  skip_final_snapshot       = true
}

locals {
  database_url = "postgresql://${aws_lightsail_database.ledger.master_username}:${urlencode(random_password.db_master.result)}@${aws_lightsail_database.ledger.master_endpoint_address}:${aws_lightsail_database.ledger.master_endpoint_port}/${aws_lightsail_database.ledger.master_database_name}"
}
