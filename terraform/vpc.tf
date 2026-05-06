# vpc.tf - Hardcoded for Learner Lab environment
locals {
  vpc_id = var.vpc_id
  
  # Map variables to locals used in other files
  private_subnets = var.private_subnet_ids
  public_subnets  = var.public_subnet_ids
  
  # Use the default SG ID from your previous error log
  default_sg_id   = "sg-0548a35be0d988d6b"
}
