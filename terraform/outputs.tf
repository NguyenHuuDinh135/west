output "vpc_id" {
  value = aws_vpc.main.id
}

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  value = aws_ecs_service.main.name
}

output "dynamodb_table_name" {
  value = aws_dynamodb_table.user_profiles.name
}

output "redis_endpoint" {
  value = aws_elasticache_replication_group.main.primary_endpoint_address
}
