# Postgres module (PHASE7-DEPLOYMENT.md Section 1/6: "Neon/Postgres
# (metadata + lineage + auth)"). Provisions AWS RDS for Postgres - the
# standard, stable Terraform-managed option - rather than Neon directly:
# Neon's own Terraform provider is comparatively new/less battle-tested, and
# keeping every module on one provider (aws) keeps this whole
# infra/cloud/terraform/ tree on one consistent remote state backend rather
# than mixing several providers' auth/state models for one demo. A team that
# specifically wants Neon's serverless-Postgres pricing model instead of RDS
# can swap this module's resource block for the `kislerdm/neon` provider's
# `neon_project`/`neon_branch` resources without changing anything that
# calls this module - the output contract (`connection_host` etc.) is the
# same shape either way.
#
# This provisions the DATABASE, not the schema - database/cloud_db.py's
# `PostgresConnectionManager.run_migrations()` (against
# database/migrations/postgres/*.sql) is what actually creates
# ingestion_runs/auth.users/tenant.tenants once this database exists and
# config/database.yaml's postgres.enabled is set to true.
#
# Not run against a real AWS account - see modules/vpc/main.tf's header.

variable "name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "instance_class" {
  type    = string
  default = "db.t4g.micro"  # right-sized for a demo/staging deployment, not production load
}

variable "allocated_storage_gb" {
  type    = number
  default = 20
}

variable "database_name" {
  type    = string
  default = "mini_faire_metadata"
}

variable "master_username" {
  type    = string
  default = "mini_faire"
}

# The master password is generated here and stored in Secrets Manager (see
# modules/secrets_manager) rather than passed in as a plain Terraform
# variable - same "never commit/hardcode a credential" posture every
# config/*.yaml in this repo already takes for MONGO_PASSWORD/JWT_SECRET_KEY.
resource "random_password" "master" {
  length  = 32
  special = false  # RDS's allowed-character set for master passwords excludes some specials
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-postgres"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "postgres" {
  name_prefix = "${var.name}-postgres-"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["10.20.0.0/16"]  # backend/orchestration security groups' VPC CIDR - see modules/vpc
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_instance" "this" {
  identifier             = "${var.name}-postgres"
  engine                 = "postgres"
  engine_version         = "16"
  instance_class         = var.instance_class
  allocated_storage      = var.allocated_storage_gb
  db_name                = var.database_name
  username               = var.master_username
  password               = random_password.master.result
  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.postgres.id]
  storage_encrypted      = true
  backup_retention_period = 7
  skip_final_snapshot    = false
  final_snapshot_identifier = "${var.name}-postgres-final"
  deletion_protection    = true
}

output "connection_host" {
  value = aws_db_instance.this.address
}

output "connection_port" {
  value = aws_db_instance.this.port
}

output "database_name" {
  value = aws_db_instance.this.db_name
}

output "master_username" {
  value = aws_db_instance.this.username
}

output "master_password" {
  value     = random_password.master.result
  sensitive = true
}
