# Media + backups buckets (doc 05 §2). SSE on; audio lifecycle-deleted at 90d.

resource "aws_s3_bucket" "media" {
  bucket = "opd-${var.env}-media"
  tags   = { Name = "opd-${var.env}-media" }
}

resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = aws_s3_bucket.media.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    id     = "expire-audio-90d"
    status = "Enabled"
    filter {
      prefix = "audio/"
    }
    expiration {
      days = 90
    }
  }
}

resource "aws_s3_bucket" "backups" {
  bucket = "opd-${var.env}-backups"
  tags   = { Name = "opd-${var.env}-backups" }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    id     = "expire-backups-35d"
    status = "Enabled"
    filter {}
    expiration {
      days = 35
    }
  }
}
