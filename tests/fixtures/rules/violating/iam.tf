# IAM group: iam.action.wildcard, iam.resource.wildcard,
# iam.trust.wildcard_principal, iam.policy.admin_attached.

# iam.trust.wildcard_principal -- anyone in any AWS account may assume this role.
resource "aws_iam_role" "wide_open" {
  name = "plimsoll-fixture-wide-open"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { AWS = "*" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

# iam.action.wildcard and iam.resource.wildcard, both from one statement.
resource "aws_iam_role_policy" "everything" {
  name = "plimsoll-fixture-everything"
  role = aws_iam_role.wide_open.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "*"
        Resource = "*"
      }
    ]
  })
}

# iam.policy.admin_attached
resource "aws_iam_role_policy_attachment" "admin" {
  role       = aws_iam_role.wide_open.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
