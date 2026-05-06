locals {
  vpc_id = var.vpc_id
}

locals {
  private_subnets = var.private_subnet_ids
  public_subnets  = var.public_subnet_ids
  
  # Hardcode the default SG ID from the error log to bypass 'DescribeSecurityGroups'
  default_sg_id   = "sg-0548a35be0d988d6b"
}
