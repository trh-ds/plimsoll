# S3 group, fixed.
#
# Two buckets, because the fix for `s3.logging.missing` needs somewhere to send the logs.
# The log bucket is exempt from the logging rule and from nothing else: requiring it to
# log its own reads either recurses or forces a third bucket that also needs one, and the
# rule ends up unsatisfiable, which is how a rule gets suppressed rather than fixed.

resource "aws_s3_bucket" "exposed" {
  bucket = "plimsoll-fixture-compliant-reports"
}

# s3.acl.public
resource "aws_s3_bucket_acl" "exposed" {
  bucket = aws_s3_bucket.exposed.id
  acl    = "private"
}

# s3.versioning.disabled
resource "aws_s3_bucket_versioning" "exposed" {
  bucket = aws_s3_bucket.exposed.id

  versioning_configuration {
    status = "Enabled"
  }
}

# s3.public_access_block.missing
resource "aws_s3_bucket_public_access_block" "exposed" {
  bucket                  = aws_s3_bucket.exposed.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# s3.encryption.missing
resource "aws_s3_bucket_server_side_encryption_configuration" "exposed" {
  bucket = aws_s3_bucket.exposed.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

# s3.logging.missing
resource "aws_s3_bucket_logging" "exposed" {
  bucket        = aws_s3_bucket.exposed.id
  target_bucket = aws_s3_bucket.logs.id
  target_prefix = "reports/"
}

# The log bucket. Also the CloudTrail destination, so one bucket serves both fixes.
resource "aws_s3_bucket" "logs" {
  bucket = "plimsoll-fixture-compliant-logs"
}

resource "aws_s3_bucket_versioning" "logs" {
  bucket = aws_s3_bucket.logs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "logs" {
  bucket                  = aws_s3_bucket.logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}
