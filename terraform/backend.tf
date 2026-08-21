terraform {
  backend "s3" {
    bucket       = "ledger-terraform-state-302127759466"
    key          = "ledger/prod/terraform.tfstate"
    region       = "us-east-1"
    profile      = "ledger"
    encrypt      = true
    use_lockfile = true
  }
}
