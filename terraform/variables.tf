variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name"
  type        = string
  default     = "west"
}

variable "vpc_cidr" {
  description = "VPC CIDR"
  type        = string
  default     = "10.20.0.0/16"
}

variable "ecs_task_cpu" {
  description = "ECS Task CPU"
  type        = string
  default     = "256"
}

variable "ecs_task_memory" {
  description = "ECS Task Memory"
  type        = string
  default     = "512"
}
