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

# Deployments (including the very first one) are intentionally NOT managed
# here. `principal_arn` is available as soon as the service itself exists,
# regardless of deployment state, so the ECR policy below doesn't need one.
# The first real deployment is created via `aws lightsail
# create-container-service-deployment` using our own tested image (Phase 4/6
# verification); every deployment after that comes from the CD pipeline.
