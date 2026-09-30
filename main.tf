# Authentication is supplied through GITHUB_TOKEN, never through tfvars.
provider "github" {
  owner    = var.organization
  base_url = var.github_api_url
}

# Empty map removes only memberships already tracked by this state.
resource "github_membership" "members" {
  for_each = var.members

  username             = each.key
  role                 = each.value
  downgrade_on_destroy = false
}
