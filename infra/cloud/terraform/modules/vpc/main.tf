# VPC module (PHASE7-DEPLOYMENT.md Section 1: "infra/cloud/terraform/ With
# modules for: VPC..."). AWS is this repo's primary Terraform target - see
# infra/cloud/terraform/main.tf's header for why one provider was picked
# rather than mixing several with no consistent remote state backend.
#
# Not run against a real AWS account (no `terraform` binary or AWS
# credentials in the sandbox that authored this file - `terraform validate`
# could not be run here). Written to current AWS provider (~> 5.0) syntax.

variable "name" {
  type        = string
  description = "Prefix for every resource this module creates, e.g. \"mini-faire-prod\"."
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "availability_zone_count" {
  type    = number
  default = 2
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = { Name = "${var.name}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-igw" }
}

# One public subnet per AZ (the load balancer / API gateway live here) and
# one private subnet per AZ (Postgres, and any future ECS/Fargate compute -
# infra/cloud/terraform/main.tf wires those into the private subnets).
resource "aws_subnet" "public" {
  count                   = var.availability_zone_count
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.name}-public-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = var.availability_zone_count
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + var.availability_zone_count)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = { Name = "${var.name}-private-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = { Name = "${var.name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = var.availability_zone_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "backend" {
  name_prefix = "${var.name}-backend-"
  vpc_id      = aws_vpc.this.id

  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]  # only the load balancer (inside this VPC) reaches the backend directly
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "${var.name}-backend-sg" }
}

output "vpc_id" {
  value = aws_vpc.this.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "backend_security_group_id" {
  value = aws_security_group.backend.id
}
