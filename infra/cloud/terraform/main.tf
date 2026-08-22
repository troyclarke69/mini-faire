# Root Terraform config (PHASE7-DEPLOYMENT.md Section 1) wiring every
# module in infra/cloud/terraform/modules/ together into one deployable
# stack: `terraform init && terraform plan` from this directory.
#
# AWS was picked as the one consistent provider for this whole tree
# (mongodb_atlas is the sole exception - Atlas is managed outside AWS
# regardless, see modules/mongodb_atlas/main.tf's header) - Fly.io/Render
# don't have mature first-party Terraform providers as of this writing, so
# infra/cloud/fly.toml / render.yaml stay separate, imperative-CLI-driven
# manifests rather than Terraform resources; a team standardizing on AWS
# compute (ECS/Fargate/Container Apps-equivalent) instead of Fly/Render
# would add that as another module here, using modules/vpc's and
# modules/load_balancer's outputs the same way modules/postgres does.
#
# Not run against a real AWS account or initialized against a real state
# backend - no `terraform` binary available in the sandbox that authored
# this file (network access to releases.hashicorp.com was not available
# either, so installing one to self-check wasn't possible). Written to
# current (~> 5.0) AWS provider syntax and cross-checked against each
# resource's documented required arguments; `terraform validate` from a
# machine with the binary installed is the way to confirm it for real
# before applying.

terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state - commented out rather than hardcoded to a bucket this repo
  # doesn't own. Uncomment and fill in once modules/object_storage (or a
  # bucket provisioned by hand, chicken-and-egg-free) exists:
  # backend "s3" {
  #   bucket         = "mini-faire-terraform-state"
  #   key            = "cloud/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "mini-faire-terraform-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  type        = string
  default     = "mini-faire"
  description = "Prefix applied to every resource name across every module."
}

variable "jwt_secret" {
  type      = string
  sensitive = true
}

variable "mongo_atlas_org_id" {
  type    = string
  default = ""
}

variable "enable_mongo_atlas" {
  type    = bool
  default = false
}

variable "enable_postgres" {
  type    = bool
  default = false
}

module "vpc" {
  source                  = "./modules/vpc"
  name                    = var.name
  availability_zone_count = 2
}

module "object_storage" {
  source      = "./modules/object_storage"
  name        = var.name
  bucket_name = "${var.name}-raw"
}

module "load_balancer" {
  source                     = "./modules/load_balancer"
  name                       = var.name
  vpc_id                     = module.vpc.vpc_id
  public_subnet_ids          = module.vpc.public_subnet_ids
  backend_security_group_id  = module.vpc.backend_security_group_id
}

module "api_gateway" {
  source                    = "./modules/api_gateway"
  name                      = var.name
  backend_load_balancer_dns = module.load_balancer.dns_name
}

module "postgres" {
  source             = "./modules/postgres"
  count              = var.enable_postgres ? 1 : 0
  name               = var.name
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
}

module "mongodb_atlas" {
  source       = "./modules/mongodb_atlas"
  count        = var.enable_mongo_atlas ? 1 : 0
  name         = var.name
  atlas_org_id = var.mongo_atlas_org_id
}

module "secrets_manager" {
  source            = "./modules/secrets_manager"
  name              = var.name
  jwt_secret        = var.jwt_secret
  postgres_password = var.enable_postgres ? module.postgres[0].master_password : ""
  mongo_password    = var.enable_mongo_atlas ? module.mongodb_atlas[0].database_password : ""
}

output "backend_load_balancer_dns" {
  value = module.load_balancer.dns_name
}

output "api_gateway_invoke_url" {
  value = module.api_gateway.invoke_url
}

output "raw_bucket_name" {
  value = module.object_storage.bucket_name
}

output "postgres_connection_host" {
  value = var.enable_postgres ? module.postgres[0].connection_host : null
}

output "mongo_atlas_srv_uri" {
  value = var.enable_mongo_atlas ? module.mongodb_atlas[0].connection_srv_uri : null
}
