# PoC: GitHub Org Members bằng Terraform

Repo này giữ PoC quản lý thành viên GitHub Organization `xbrain-org-poc` bằng `github_membership`. Cấu trúc thư mục theo [`xbrain-org-poc/github-org-iac`](https://github.com/xbrain-org-poc/github-org-iac): mỗi phạm vi có thư mục và Terraform state riêng dưới `live/<org>/`.

| Thư mục | Quản lý |
| --- | --- |
| [`live/xbrain-org-poc/identity/`](live/xbrain-org-poc/identity/) | Thành viên org và role qua `github_membership`. Code, hướng dẫn chạy và bằng chứng PoC |

Chạy Terraform và script trong `live/xbrain-org-poc/identity/`, không chạy tại root. Không commit token, `terraform.tfvars.json`, state hoặc binary plan. Hướng dẫn chi tiết nằm trong [README của identity](live/xbrain-org-poc/identity/README.md).
