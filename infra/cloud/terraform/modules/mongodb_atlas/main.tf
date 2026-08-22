# MongoDB Atlas module (PHASE7-DEPLOYMENT.md Section 1/6: "MongoDB Atlas
# (events + streaming)"). Uses HashiCorp's official `mongodb/mongodbatlas`
# provider - the one module in this tree NOT on the `aws` provider, because
# Atlas is managed outside AWS regardless (even AWS-hosted Atlas clusters
# are provisioned through Atlas' own control plane/API, not AWS resources).
#
# Mirrors config/mongo.yaml's existing shape (database name `rmap`,
# `updated_at` watermark field convention documented there) rather than
# inventing a new one - this module provisions the CLUSTER/PROJECT, the
# same Atlas project ingestion/mongo_ingest.py's mongo_uri_template already
# points at in a real deployment.
#
# Requires MONGODB_ATLAS_PUBLIC_KEY / MONGODB_ATLAS_PRIVATE_KEY (Atlas API
# keys, generated in the Atlas console - never committed here). Not run
# against a real Atlas org from the sandbox that authored this file.

terraform {
  required_providers {
    mongodbatlas = {
      source  = "mongodb/mongodbatlas"
      version = "~> 1.16"
    }
  }
}

variable "name" {
  type = string
}

variable "atlas_org_id" {
  type        = string
  description = "MongoDB Atlas organization ID (from the Atlas console)."
}

variable "cluster_tier" {
  type    = string
  default = "M10"  # smallest dedicated tier with backups/VPC peering; M0 (free) has neither
}

variable "region" {
  type    = string
  default = "US_EAST_1"
}

resource "mongodbatlas_project" "this" {
  name   = var.name
  org_id = var.atlas_org_id
}

resource "mongodbatlas_cluster" "this" {
  project_id                 = mongodbatlas_project.this.id
  name                       = "${var.name}-cluster"
  provider_name              = "AWS"
  provider_region_name       = var.region
  provider_instance_size_name = var.cluster_tier
  mongo_db_major_version     = "7.0"
  backup_enabled             = true
}

resource "mongodbatlas_database_user" "ingestion" {
  project_id         = mongodbatlas_project.this.id
  username           = "mini_faire_ingestion"
  password           = random_password.mongo_user.result
  auth_database_name = "admin"

  roles {
    role_name     = "readWrite"
    database_name = "rmap"  # config/mongo.yaml's `database` field
  }
}

resource "random_password" "mongo_user" {
  length  = 32
  special = false
}

# Atlas requires an explicit IP allowlist (or VPC peering, out of scope for
# this module's minimal footprint) - 0.0.0.0/0 here is the "works from any
# deployment target without extra plumbing" default a demo needs; a real
# deployment should replace this with the VPC's NAT gateway IP or Atlas VPC
# peering into modules/vpc's VPC.
resource "mongodbatlas_project_ip_access_list" "allow_all" {
  project_id = mongodbatlas_project.this.id
  cidr_block = "0.0.0.0/0"
  comment    = "TODO: replace with the deployment's actual egress IP / VPC peering before production use"
}

output "connection_srv_uri" {
  value = mongodbatlas_cluster.this.srv_address
}

output "database_user" {
  value = mongodbatlas_database_user.ingestion.username
}

output "database_password" {
  value     = random_password.mongo_user.result
  sensitive = true
}
