output "container_service_url" {
  value = aws_lightsail_container_service.ledger.url
}

output "ecr_repository_url" {
  value = aws_ecr_repository.ledger.repository_url
}

output "cd_role_arn" {
  value = aws_iam_role.cd.arn
}

output "database_endpoint" {
  value = aws_lightsail_database.ledger.master_endpoint_address
}
