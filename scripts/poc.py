#!/usr/bin/env python3
"""Terraform evidence runner and independent GitHub verification (stdlib only)."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"


def load_local_token():
    """Read the local credential as data, never execute .env as shell code."""
    if os.environ.get("GITHUB_TOKEN"):
        return
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = re.fullmatch(r"\s*(?:export\s+)?GITHUB_TOKEN\s*=\s*(.*?)\s*", line)
        if match:
            token = match.group(1)
            if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
                token = token[1:-1]
            if token and re.fullmatch(r"[A-Za-z0-9_]+", token):
                os.environ["GITHUB_TOKEN"] = token
                return
            raise RuntimeError("Local .env has an empty or invalid GITHUB_TOKEN value.")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def run_tf(args, directory, label, accepted=(0,)):
    result = subprocess.run(["terraform", *args], cwd=ROOT, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # Defense in depth; Terraform debug logging must remain disabled.
    output = result.stdout
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        output = output.replace(token, "[REDACTED]")
    (directory / f"{label}.txt").write_text(output, encoding="utf-8")
    print(output, end="")
    write_json(directory / f"{label}.exit.json", {"exit_code": result.returncode})
    if result.returncode not in accepted:
        raise RuntimeError(f"terraform {args[0]} failed: exit {result.returncode}")
    return output, result.returncode


def config():
    path = ROOT / "terraform.tfvars.json"
    if not path.exists():
        raise RuntimeError("Create terraform.tfvars.json using the example and approved targets.")
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    org = cfg["organization"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", org):
        raise RuntimeError("Invalid organization login")
    base = urlsplit(cfg["github_api_url"])
    if (base.scheme != "https" or not base.hostname or base.username or base.password
            or base.query or base.fragment or not base.path.endswith("/")):
        raise RuntimeError("Use an HTTPS API base URL with trailing / and no credentials/query.")
    if not os.environ.get("GITHUB_TOKEN"):
        raise RuntimeError("Set GITHUB_TOKEN in this process; do not put it in files or arguments.")
    # Provider v6 has legacy environment overrides for owner/base_url.
    for name in ("GITHUB_OWNER", "GITHUB_ORGANIZATION", "GITHUB_BASE_URL"):
        if os.environ.get(name):
            raise RuntimeError(f"Unset {name}; use terraform.tfvars.json as the single target source.")
    for name in os.environ:
        if name.startswith(("TF_VAR_", "TF_CLI_ARGS")):
            raise RuntimeError(f"Unset {name} to avoid overriding this PoC configuration.")
    extra = list(ROOT.glob("*.auto.tfvars*")) + list(ROOT.glob("terraform.tfvars"))
    if extra:
        raise RuntimeError("Remove additional automatic variable files for this PoC.")
    return cfg


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("API redirect refused; verify configured GitHub API URL.")


def github_get(cfg, path, allow_404=False):
    request = Request(cfg["github_api_url"] + path, headers={
        "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
        "Accept": "application/vnd.github+json",
        "User-Agent": "terraform-org-membership-poc",
    })
    # Use server default API version for GHES compatibility; record selected API URL.
    try:
        with build_opener(NoRedirect).open(request, timeout=30) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        if error.code == 404 and allow_404:
            return 404, None
        raise RuntimeError(f"GitHub GET {path}: HTTP {error.code}; verify auth, permissions and policy.") from None


def github_list(cfg, path):
    items = []
    page = 1
    while True:
        _, batch = github_get(cfg, f"{path}?per_page=100&page={page}")
        items.extend(batch)
        if len(batch) < 100:
            return items
        page += 1


def verify(cfg, username, expected, role, directory):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", username):
        raise RuntimeError("Invalid username")
    org = cfg["organization"]
    # Private membership and invitation listing must succeed before interpreting 404.
    github_get(cfg, f"orgs/{org}")
    invitations = github_list(cfg, f"orgs/{org}/invitations")
    members = github_list(cfg, f"orgs/{org}/members")
    status, membership = github_get(cfg, f"orgs/{org}/memberships/{username}", True)
    pending = [i for i in invitations if (i.get("login") or "").lower() == username.lower()]
    active = any(m["login"].lower() == username.lower() for m in members)
    actual = membership["state"] if membership else "absent"
    actual_role = membership["role"] if membership else None
    checks = {"state_matches": actual == expected}
    if expected == "absent":
        checks.update(no_pending_invitation=not pending, not_in_member_list=not active,
                      membership_returns_404=status == 404)
    elif expected == "pending":
        checks.update(pending_invitation_exists=bool(pending), not_active=not active)
    else:
        checks.update(in_member_list=active, no_pending_invitation=not pending)
    if role:
        checks["role_matches"] = actual_role == role
    result = {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "organization": org, "api_url": cfg["github_api_url"], "username": username,
        "membership_http_status": status, "state": actual, "role": actual_role,
        "active_member_list_match": active,
        "pending_invitations": [{k: i.get(k) for k in ("id", "login", "role", "created_at")}
                                for i in pending],
        "checks": checks, "passed": all(checks.values()),
    }
    write_json(directory / "github-verification.json", result)
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise RuntimeError("GitHub verification did not match expectations; evidence retained.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    plan = sub.add_parser("plan")
    plan.add_argument("--expect-exit", type=int, choices=(0, 2), required=True)
    plan.add_argument("--refresh-only", action="store_true")
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    sub.add_parser("state")
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--username", required=True)
    check = sub.add_parser("verify")
    check.add_argument("--username", required=True)
    check.add_argument("--state", choices=("pending", "active", "absent"), required=True)
    check.add_argument("--role", choices=("member", "admin"))
    args = parser.parse_args()
    load_local_token()
    if any(os.environ.get(k) for k in ("TF_LOG", "TF_LOG_PROVIDER", "TF_LOG_CORE", "TF_LOG_PATH")):
        raise RuntimeError("Disable Terraform debug logging before running with credentials.")
    directory = EVIDENCE / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + args.command)
    directory.mkdir(parents=True)
    print(f"Evidence: {directory}")
    if args.command == "validate":
        run_tf(["fmt", "-check", "-recursive"], directory, "fmt")
        run_tf(["init", "-input=false"], directory, "init")
        run_tf(["validate", "-no-color"], directory, "validate")
        run_tf(["version", "-json"], directory, "versions")
        return
    cfg = config()
    write_json(directory / "target.json", cfg)
    if args.command == "preflight":
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", args.username):
            raise RuntimeError("Invalid username")
        _, actor = github_get(cfg, "user")
        _, target = github_get(cfg, f"users/{args.username}")
        _, membership = github_get(cfg, f"orgs/{cfg['organization']}/memberships/{actor['login']}")
        result = {"actor": actor["login"], "target": target["login"],
                  "organization": cfg["organization"], "actor_state": membership["state"],
                  "actor_role": membership["role"]}
        write_json(directory / "preflight.json", result)
        print(json.dumps(result, indent=2))
        if actor["login"].lower() == target["login"].lower():
            raise RuntimeError("Test target is the PAT owner; use a separate test account.")
        if membership["state"] != "active" or membership["role"] != "admin":
            raise RuntimeError("PAT actor must be an active organization owner.")
    elif args.command == "verify":
        verify(cfg, args.username, args.state, args.role, directory)
    elif args.command == "plan":
        flags = ["-refresh-only"] if args.refresh_only else []
        path = directory / "change.tfplan"
        _, code = run_tf(["plan", "-input=false", "-no-color", "-detailed-exitcode",
                          *flags, f"-out={path}"], directory, "plan", (0, 2))
        raw, _ = run_tf(["show", "-json", str(path)], directory, "plan-json")
        plan_data = json.loads(raw)
        write_json(directory / "actions.json", [
            {"address": r["address"], "actions": r["change"]["actions"]}
            for r in plan_data.get("resource_changes", [])])
        if code != args.expect_exit:
            raise RuntimeError(f"Expected plan exit {args.expect_exit}, got {code}")
        write_json(directory / "approved-input.json", {
            "config": cfg, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    elif args.command == "apply":
        path = args.plan.resolve()
        if not path.is_relative_to(EVIDENCE.resolve()) or path.name != "change.tfplan":
            raise RuntimeError("Use an evidence/.../change.tfplan created by this runner.")
        manifest = json.loads((path.parent / "approved-input.json").read_text())
        if manifest["config"] != cfg or manifest["sha256"] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise RuntimeError("Config or saved plan changed; create and review a new plan.")
        run_tf(["apply", "-input=false", "-no-color", str(path)], directory, "apply")
        run_tf(["show", "-json"], directory, "state-json")
    else:
        run_tf(["state", "list"], directory, "state-list")
        run_tf(["show", "-json"], directory, "state-json")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
