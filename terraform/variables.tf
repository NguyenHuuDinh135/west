variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-southeast-1"
}

variable "project_name" {
  description = "Project name"
  type        = string
  default     = "ddb-cache-lab"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.20.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "Public subnet CIDR blocks"
  type        = list(string)
  default     = ["10.20.1.0/24", "10.20.2.0/24"]
}

variable "private_subnet_cidrs" {
  description = "Private subnet CIDR blocks"
  type        = list(string)
  default     = ["10.20.11.0/24", "10.20.12.0/24"]
}

variable "redis_node_type" {
  description = "Redis node type"
  type        = string
  default     = "cache.t4g.micro"
}

variable "ecs_task_cpu" {
  description = "ECS task CPU"
  type        = number
  default     = 256
}

variable "ecs_task_memory" {
  description = "ECS task memory"
  type        = number
  default     = 512
}
