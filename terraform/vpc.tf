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

# For the lab, we'll use the subnets provided or partition the existing ones
locals {
  # If user provided subnets, use them. Otherwise, try to auto-select.
  # Note: In many lab environments, you might only have a few subnets.
  public_subnets  = length(var.public_subnet_ids) > 0 ? var.public_subnet_ids : [data.aws_subnets.all.ids[0]]
  private_subnets = length(var.private_subnet_ids) > 0 ? var.private_subnet_ids : slice(data.aws_subnets.all.ids, 0, min(2, length(data.aws_subnets.all.ids)))
}

# Gateway Endpoints (Usually allowed even with strict SCP)
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = data.aws_vpc.selected.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = data.aws_route_tables.selected.ids

  tags = {
    Name = "${var.project_name}-s3-endpoint"
  }
}

data "aws_route_tables" "selected" {
  vpc_id = data.aws_vpc.selected.id
}

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = data.aws_vpc.selected.id
  service_name      = "com.amazonaws.${var.aws_region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = data.aws_route_tables.selected.ids

  tags = {
    Name = "${var.project_name}-dynamodb-endpoint"
  }
}

# Interface Endpoints Security Group
resource "aws_security_group" "endpoints" {
  name        = "${var.project_name}-endpoints-sg"
  description = "Allow HTTPS to VPC interface endpoints from within VPC"
  vpc_id      = data.aws_vpc.selected.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.selected.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-endpoints-sg"
  }
}

# Interface Endpoints
resource "aws_vpc_endpoint" "ecr_api" {
  vpc_id              = data.aws_vpc.selected.id
  service_name        = "com.amazonaws.${var.aws_region}.ecr.api"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = local.private_subnets
  security_group_ids  = [aws_security_group.endpoints.id]
}

resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id              = data.aws_vpc.selected.id
  service_name        = "com.amazonaws.${var.aws_region}.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = local.private_subnets
  security_group_ids  = [aws_security_group.endpoints.id]
}

resource "aws_vpc_endpoint" "logs" {
  vpc_id              = data.aws_vpc.selected.id
  service_name        = "com.amazonaws.${var.aws_region}.logs"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = local.private_subnets
  security_group_ids  = [aws_security_group.endpoints.id]
}

resource "aws_vpc_endpoint" "secretsmanager" {
  vpc_id              = data.aws_vpc.selected.id
  service_name        = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = local.private_subnets
  security_group_ids  = [aws_security_group.endpoints.id]
}
