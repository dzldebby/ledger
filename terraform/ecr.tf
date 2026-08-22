resource "aws_ecr_repository" "ledger" {
  name                 = "ledger"
  image_tag_mutability = "IMMUTABLE"

  # AWS refuses to delete a repository that still holds images, which would
  # fail `terraform destroy` partway through. This stack is torn down between
  # sessions by design, so discarding the images with it is intended. A stack
  # holding images worth keeping should leave this false.
  force_delete = true

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
        Sid    = "AllowLightsailPull"
        Effect = "Allow"
        Principal = {
          # Must be the *ECR image puller* role, not the container service's
          # general principal_arn - they are two different ARNs. Granting the
          # wrong one makes the image pull fail silently: Lightsail creates no
          # container, emits no logs, and just reports the deployment
          # "Canceled".
          AWS = aws_lightsail_container_service.ledger.private_registry_access[0].ecr_image_puller_role[0].principal_arn
        }
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
      }
    ]
  })
}
