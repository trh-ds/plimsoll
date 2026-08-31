# IAM group, fixed.

# iam.trust.wildcard_principal: the service that assumes this role is named.
resource "aws_iam_role" "wide_open" {
  name = "plimsoll-fixture-scoped"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ec2.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

# iam.action.wildcard and iam.resource.wildcard: named actions, named ARNs.
resource "aws_iam_role_policy" "everything" {
  name = "plimsoll-fixture-reports-read"
  role = aws_iam_role.wide_open.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::plimsoll-fixture-compliant-reports",
          "arn:aws:s3:::plimsoll-fixture-compliant-reports/*",
        ]
      },
      {
        # Actions AWS only accepts against "*". The rule knows about these, and a rule
        # that cried wolf here would teach reviewers to skip it.
        Effect   = "Allow"
        Action   = "sts:GetCallerIdentity"
        Resource = "*"
      },
    ]
  })
}

# iam.policy.admin_attached: a policy scoped to what this identity actually does.
resource "aws_iam_role_policy_attachment" "admin" {
  role       = aws_iam_role.wide_open.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
