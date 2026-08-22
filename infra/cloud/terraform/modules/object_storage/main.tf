# Object storage module (PHASE7-DEPLOYMENT.md Section 1/5: "S3-compatible
# object storage"). Provisions the bucket config/storage.yaml's `s3` backend
# (storage/cloud_storage.py's S3StorageBackend) points at - same bucket
# name/versioning/retention defaults that file documents, so the Terraform
# module and the application config agree without hand-syncing values.

variable "name" {
  type = string
}

variable "bucket_name" {
  type    = string
  default = "mini-faire-raw"  # matches config/storage.yaml's s3.bucket default
}

variable "retention_days" {
  type    = number
  default = 365  # matches config/storage.yaml's retention.raw_zone_days default
}

resource "aws_s3_bucket" "raw" {
  bucket = var.bucket_name
  tags   = { Name = "${var.name}-raw" }
}

# storage/cloud_storage.py's upload_raw_json() keeps its own local-backend
# version history (`<key>.vN.json` siblings) when versioning_enabled - this
# turns on the S3-native equivalent so the S3 backend gets the same
# behavior through the bucket itself rather than needing extra client-side
# object writes.
resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    id     = "raw-zone-retention"
    status = "Enabled"
    expiration {
      days = var.retention_days
    }
    noncurrent_version_expiration {
      noncurrent_days = var.retention_days
    }
  }
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

output "bucket_name" {
  value = aws_s3_bucket.raw.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.raw.arn
}
