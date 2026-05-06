# Use existing OIDC Provider
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

# The user already has a role named 'GithubFull' configured in their workflow.
# We will just fetch it to provide as an output if needed, but not try to create it.
data "aws_iam_role" "github_actions" {
  name = "GithubFull"
}
