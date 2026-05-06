# Use existing VPC
data "aws_vpc" "selected" {
  id      = var.vpc_id != "" ? var.vpc_id : null
  default = var.vpc_id == "" ? true : null
}

# Fetch the DEFAULT security group of the VPC
data "aws_security_group" "default" {
  vpc_id = data.aws_vpc.selected.id
  name   = "default"
}

# Fetch subnets
data "aws_subnets" "all" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.selected.id]
  }
}

data "aws_route_tables" "selected" {
  vpc_id = data.aws_vpc.selected.id
}

locals {
  public_subnets  = length(var.public_subnet_ids) > 0 ? var.public_subnet_ids : [data.aws_subnets.all.ids[0]]
  private_subnets = length(var.private_subnet_ids) > 0 ? var.private_subnet_ids : slice(data.aws_subnets.all.ids, 0, min(2, length(data.aws_subnets.all.ids)))
  
  # Use the existing default security group since CreateSecurityGroup is blocked
  default_sg_id   = data.aws_security_group.default.id
}
