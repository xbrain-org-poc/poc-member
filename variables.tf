variable "organization" {
  description = "GitHub organization login explicitly approved for this PoC."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9-]*$", var.organization))
    error_message = "Provide an organization login, not a URL."
  }
}

variable "github_api_url" {
  description = "Explicit REST API base URL, including trailing slash. GitHub.com: https://api.github.com/; GHES: https://HOST/api/v3/."
  type        = string
  nullable    = false
  validation {
    condition     = can(regex("^https://[^/?#]+(/[^?#]*)?/$", var.github_api_url))
    error_message = "Use an HTTPS API base URL ending in /."
  }
}

variable "members" {
  description = "Desired managed membership subset: GitHub username => member or admin (organization owner)."
  type        = map(string)
  nullable    = false
  validation {
    condition = alltrue([
      for username, role in var.members :
      can(regex("^[a-z0-9][a-z0-9-]*$", username)) && contains(["member", "admin"], role)
    ])
    error_message = "Use lowercase GitHub usernames and roles member or admin."
  }
}
