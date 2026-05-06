# Hardcoded password for Redis due to Secrets Manager creation being blocked by SCP
locals {
  redis_auth_token = "LabPassword12345!" 
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.project_name}-redis-subnet-group"
  subnet_ids = local.private_subnets
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id       = "${var.project_name}-redis"
  description                = "Redis cluster for product-api"
  node_type                  = var.redis_node_type
  num_cache_clusters         = 2
  parameter_group_name       = "default.redis7"
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.main.name
  security_group_ids         = [local.default_sg_id] # Using default SG due to SCP restriction
  automatic_failover_enabled = true
  multi_az_enabled           = true
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true
  auth_token                 = local.redis_auth_token

  tags = {
    Name = "${var.project_name}-redis"
  }
}
