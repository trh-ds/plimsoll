# S3 group: s3.acl.public, s3.public_access_block.missing, s3.encryption.missing,
# s3.versioning.disabled, s3.logging.missing.
#
# Four of the five are absence rules, which is the shape a model hits most often: it
# writes the bucket it was asked for and stops, because "create a bucket for reports"
# says nothing about encryption.

# No public access block, no encryption, no logging.
resource "aws_s3_bucket" "exposed" {
  bucket = "plimsoll-fixture-violating-exposed"
}

# s3.acl.public
resource "aws_s3_bucket_acl" "exposed" {
  bucket = aws_s3_bucket.exposed.id
  acl    = "public-read"
}

# s3.versioning.disabled -- present and explicitly not Enabled, which is the case an
# absence check alone would miss.
resource "aws_s3_bucket_versioning" "exposed" {
  bucket = aws_s3_bucket.exposed.id

  versioning_configuration {
    status = "Suspended"
  }
}
