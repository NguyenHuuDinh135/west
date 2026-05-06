resource "aws_ecr_repository" "app" {
  name                 = "${var.project_name}-app"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  force_delete = true # For easier lab cleanup
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}
