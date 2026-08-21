resource "aws_lightsail_container_service" "ledger" {
  name  = "ledger"
  power = var.container_power
  scale = var.container_scale

  private_registry_access {
    ecr_image_puller_role {
      is_active = true
    }
  }
}

# Initial placeholder deployment so the service has something running and
# `principal_arn` resolves for the ECR policy above. The CD pipeline pushes
# the real app image and creates a superseding deployment version on every
# push to main.
resource "aws_lightsail_container_service_deployment_version" "initial" {
  service_name = aws_lightsail_container_service.ledger.name

  container {
    container_name = "ledger"
    image          = "amazon/amazon-lightsail:hello-world"

    environment = {
      DATABASE_URL          = local.database_url
      RATE_LIMIT_PER_MINUTE = "1000"
    }

    ports = {
      "8000" = "HTTP"
    }
  }

  public_endpoint {
    container_name = "ledger"
    container_port = 8000

    health_check {
      path                = "/health"
      healthy_threshold   = 2
      unhealthy_threshold = 3
      timeout_seconds     = 5
      interval_seconds    = 10
      success_codes       = "200-299"
    }
  }
}
