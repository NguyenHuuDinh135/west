# Use existing VPC if ID is provided, otherwise fetch the default VPC
data "aws_vpc" "selected" {
  id      = var.vpc_id != "" ? var.vpc_id : null
  default = var.vpc_id == "" ? true : null
}

# Fetch subnets belonging to the selected VPC
data "aws_subnets" "all" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.selected.id]
  }
}

# Fetch route tables for the VPC (needed for existing Gateway Endpoints if any)
data "aws_route_tables" "selected" {
  vpc_id = data.aws_vpc.selected.id
}

# For the lab, we'll use the subnets provided or auto-select from existing ones
locals {
  public_subnets  = length(var.public_subnet_ids) > 0 ? var.public_subnet_ids : [data.aws_subnets.all.ids[0]]
  private_subnets = length(var.private_subnet_ids) > 0 ? var.private_subnet_ids : slice(data.aws_subnets.all.ids, 0, min(2, length(data.aws_subnets.all.ids)))
}

# Note: VPC Endpoints (S3, DDB, ECR, etc.) are removed here because 
# the SCP policy blocks their creation. We assume the environment 
# either has them or has a NAT Gateway configured.
