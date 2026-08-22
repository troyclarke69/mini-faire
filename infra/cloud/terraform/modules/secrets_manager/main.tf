# Secrets manager module (PHASE7-DEPLOYMENT.md Section 1). AWS Secrets
# Manager entries for every credential this repo's config/*.yaml files
# already document as "read from an environment variable, never
# committed" - this module is what actually populates those env vars in a
# real deployment (e.g. an ECS task definition's `secrets` block, or
# infra/cloud/azure-container-apps.backend.yaml's `keyVaultUrl` references
# for the Azure path) rather than requiring an operator to paste them in by
# hand per environment.

variable "name" {
  type = string
}

variable "jwt_secret" {
  type      = string
  sensitive = true
}

variable "mongo_password" {
  type      = string
  sensitive = true
  default   = ""
}

variable "postgres_password" {
  type      = string
  sensitive = true
  default   = ""
}

variable "slack_webhook_url" {
  type      = string
  sensitive = true
  default   = ""
}

resource "aws_secretsmanager_secret" "jwt_secret_key" {
  name = "${var.name}/JWT_SECRET_KEY"
}

resource "aws_secretsmanager_secret_version" "jwt_secret_key" {
  secret_id     = aws_secretsmanager_secret.jwt_secret_key.id
  secret_string = var.jwt_secret
}

resource "aws_secretsmanager_secret" "mongo_password" {
  name = "${var.name}/MONGO_PASSWORD"
}

resource "aws_secretsmanager_secret_version" "mongo_password" {
  secret_id     = aws_secretsmanager_secret.mongo_password.id
  secret_string = var.mongo_password
}

resource "aws_secretsmanager_secret" "postgres_password" {
  name = "${var.name}/POSTGRES_PASSWORD"
}

resource "aws_secretsmanager_secret_version" "postgres_password" {
  secret_id     = aws_secretsmanager_secret.postgres_password.id
  secret_string = var.postgres_password
}

resource "aws_secretsmanager_secret" "slack_webhook_url" {
  name = "${var.name}/SLACK_WEBHOOK_URL"
}

resource "aws_secretsmanager_secret_version" "slack_webhook_url" {
  secret_id     = aws_secretsmanager_secret.slack_webhook_url.id
  secret_string = var.slack_webhook_url
}

output "jwt_secret_key_arn" {
  value = aws_secretsmanager_secret.jwt_secret_key.arn
}

output "mongo_password_arn" {
  value = aws_secretsmanager_secret.mongo_password.arn
}

output "postgres_password_arn" {
  value = aws_secretsmanager_secret.postgres_password.arn
}

output "slack_webhook_url_arn" {
  value = aws_secretsmanager_secret.slack_webhook_url.arn
}
