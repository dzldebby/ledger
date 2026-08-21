resource "aws_ecr_repository" "ledger" {
  name                 = "ledger"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "ledger" {
  repository = aws_ecr_repository.ledger.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the last 10 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

resource "aws_ecr_repository_policy" "ledger_pull" {
  repository = aws_ecr_repository.ledger.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowLightsailPull"
        Effect    = "Allow"
        Principal = {
          AWS = aws_lightsail_container_service.ledger.principal_arn
        }
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
      }
    ]
  })
}
