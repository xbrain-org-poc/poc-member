output "managed_memberships" {
  description = "Terraform-managed IDs and roles. This does NOT prove that invitations are accepted."
  value = {
    for username, membership in github_membership.members : username => {
      id   = membership.id
      role = membership.role
    }
  }
}
