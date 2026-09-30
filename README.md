# GitHub Org Members bằng Terraform

PoC này quản lý trực tiếp một tập thành viên GitHub Organization bằng `github_membership`: mời, đổi role, và xóa. Đã chạy trên GitHub.com với provider `integrations/github` 6.13.0 và Terraform 1.16.3.

**Kết quả:** lifecycle đã PASS trên `xbrain-org-poc` / `hofang42-xbrain`. Terraform tạo invitation, giữ state khi invitation pending, cập nhật `member ↔ admin` tại chỗ, phát hiện drift, import state và xóa active member. Bằng chứng ngắn cùng sáu ảnh GitHub UI nằm trong [`evidence/README.md`](evidence/README.md).

## Chạy PoC

Terraform chạy local trong Ubuntu/WSL và gọi GitHub API. Cần Python 3.9+, Terraform 1.5+ và PAT của một organization owner. Tạo fine-grained PAT giới hạn resource owner là org thử nghiệm với **Organization permissions → Members: Read and write**; tuân theo yêu cầu approval/SSO của org. Không commit PAT.

```bash
cp terraform.tfvars.json.example terraform.tfvars.json
# Điền org, API URL và username đã được chấp thuận.
```

Tạo `.env` local (file đã bị gitignore), đặt `GITHUB_TOKEN='PAT'`, rồi chạy:

```bash
python3 scripts/poc.py validate
python3 scripts/poc.py preflight --username TEST_USER
python3 scripts/poc.py verify --username TEST_USER --state absent
python3 scripts/poc.py plan --expect-exit 2
# Review plan.txt/actions.json trong evidence run trước khi apply.
python3 scripts/poc.py apply --plan evidence/RUN_ID-plan/change.tfplan
python3 scripts/poc.py verify --username TEST_USER --state pending --role member
```

Người nhận phải tự accept invitation. Xác minh lại với `--state active`. Thay map `members` để nâng/hạ role; xóa username khỏi map để remove. Dùng plan/apply như trên cho mỗi thay đổi. Script lưu bằng chứng vào `evidence/` và dừng nếu kết quả API/plan khác mong đợi.

```bash
python3 scripts/lifecycle.py roles-and-drift
# Khi tài khoản active; demo mutates role và dùng API trực tiếp để tạo drift.
python3 scripts/lifecycle.py final-remove
```

Hai lệnh cuối là kịch bản live cố định cho org/username trong kết quả PoC; đừng chạy vào org khác. Chi tiết giới hạn, auth và state behavior có trong [`evidence/README.md`](evidence/README.md).

## Lưu ý

- `admin` trong resource tương đương Organization owner.
- Resource không phân biệt `pending` và `active`; đối chiếu membership/invitations qua API như script.
- `members = {}` chỉ xóa member được quản lý bởi state này. Dùng local state riêng cho PoC; không chạy đồng thời.
- `.env`, tfvars local, state, plan binary, browser profile và ZIP bàn giao không được commit.
