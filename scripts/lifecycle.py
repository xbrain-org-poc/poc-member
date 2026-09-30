#!/usr/bin/env python3
"""Run the explicitly authorized live PoC for xbrain-org-poc/hofang42-xbrain.

Mutates GitHub. Stops on an unexpected plan or API result. Run final-remove only
after the second invitation is accepted. All evidence remains under evidence/.
"""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from urllib.request import Request, build_opener
from urllib.error import HTTPError

import poc

ORG = "xbrain-org-poc"
USER = "hofang42-xbrain"
ADDRESS = f'github_membership.members["{USER}"]'
MEMBERSHIP_ID = f"{ORG}:{USER}"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class Lifecycle:
    def __init__(self, phase):
        poc.load_local_token()
        require(not any(os.environ.get(k) for k in ("TF_LOG", "TF_LOG_PROVIDER", "TF_LOG_CORE", "TF_LOG_PATH")),
                "Disable Terraform debug logging")
        self.cfg = poc.config()
        self.initial_role = "admin" if phase == "ui-demote" else "member"
        require(self.cfg == {"organization": ORG, "github_api_url": "https://api.github.com/",
                             "members": {USER: self.initial_role}}, "Unexpected config; refusing live lifecycle")
        self.directory = poc.EVIDENCE / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + phase)
        self.directory.mkdir()
        self.entries = []
        print(f"Evidence: {self.directory}", flush=True)
        _, actor = poc.github_get(self.cfg, "user")
        require(actor["login"].lower() != USER, "Cannot use test user's PAT")
        _, own = poc.github_get(self.cfg, f"orgs/{ORG}/memberships/{actor['login']}")
        require(own["state"] == "active" and own["role"] == "admin", "Actor is not active owner")
        poc.write_json(self.directory / "actor.json", {"login": actor["login"], "role": own["role"]})

    def stage(self, name):
        directory = self.directory / name
        directory.mkdir()
        print(name, flush=True)
        return directory

    def tf(self, args, directory, label, accepted=(0,)):
        # Full logs are saved by run_tf; keep console concise.
        with redirect_stdout(io.StringIO()):
            return poc.run_tf(args, directory, label, accepted)

    def verified(self, directory, state="active", role="member"):
        with redirect_stdout(io.StringIO()):
            poc.verify(self.cfg, USER, state, role, directory)

    def record(self, directory):
        self.entries.append({"stage": directory.name, "passed": True})
        poc.write_json(self.directory / "summary.json", self.entries)
        print(f"PASS: {directory.name}", flush=True)

    def set_members(self, members):
        self.cfg["members"] = members
        poc.write_json(poc.ROOT / "terraform.tfvars.json", self.cfg)

    def plan(self, directory, action, refresh=False):
        path = directory / "change.tfplan"
        args = ["plan", "-input=false", "-no-color", "-detailed-exitcode", f"-out={path}"]
        if refresh:
            args.append("-refresh-only")
        _, code = self.tf(args, directory, "plan", (0, 2))
        raw, _ = self.tf(["show", "-json", str(path)], directory, "plan-json")
        plan = json.loads(raw)
        require(code == (0 if action == "no-op" else 2), "Unexpected plan exit code")
        require(plan["variables"]["organization"]["value"] == ORG, "Unexpected plan org")
        require(plan["variables"]["github_api_url"]["value"] == "https://api.github.com/", "Unexpected API URL")
        changes = plan.get("resource_changes", [])
        require(all(c["address"] == ADDRESS for c in changes), "Plan includes another resource")
        if refresh:
            require(all(c["change"]["actions"] == ["no-op"] for c in changes), "Refresh would mutate resource")
            drift = plan.get("resource_drift", [])
            require(len(drift) == 1 and drift[0]["address"] == ADDRESS, "Unexpected refresh drift")
        elif action == "no-op":
            require(all(c["change"]["actions"] == ["no-op"] for c in changes), "Unexpected resource changes")
        else:
            require(len(changes) == 1 and changes[0]["change"]["actions"] == [action], "Unexpected plan action")
            if action in ("create", "update"):
                after = changes[0]["change"]["after"]
                require(after["username"] == USER and after["role"] == self.cfg["members"][USER], "Unexpected planned identity/role")
        poc.write_json(directory / "review.json", {"expected_action": action, "exit_code": code,
                                                   "refresh_only": refresh, "passed": True})
        return path

    def apply(self, directory, action, refresh=False):
        path = self.plan(directory, action, refresh)
        self.tf(["apply", "-input=false", "-no-color", str(path)], directory, "apply")
        self.tf(["show", "-json"], directory, "state-json")

    def role(self, name, role):
        directory = self.stage(name)
        self.set_members({USER: role})
        self.apply(directory, "update")
        self.verified(directory, role=role)
        state, _ = self.tf(["show", "-json"], directory, "verified-state")
        resource = json.loads(state)["values"]["root_module"]["resources"][0]
        require(resource["values"]["id"] == MEMBERSHIP_ID, "Role update replaced membership identity")
        self.plan(self.stage(name + "-converged"), "no-op")
        self.record(directory)

    def drift(self, directory, method, role=None):
        # Direct API calls deliberately bypass Terraform to produce authorized drift.
        body = json.dumps({"role": role}).encode() if role else None
        request = Request(f"https://api.github.com/orgs/{ORG}/memberships/{USER}",
                          data=body, method=method, headers={
                              "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                              "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                              "User-Agent": "terraform-org-membership-poc"})
        try:
            with build_opener(poc.NoRedirect).open(request, timeout=30) as response:
                status = response.status
                result = json.load(response) if method == "PUT" else None
        except HTTPError as error:
            raise RuntimeError(f"Drift API {method} failed with HTTP {error.code}") from None
        require(status == (200 if method == "PUT" else 204), "Unexpected drift API status")
        poc.write_json(directory / "external-change.json", {
            "method": method, "organization": ORG, "username": USER, "http_status": status,
            "state": result.get("state") if result else None, "role": result.get("role") if result else None})

    def run(self, phase):
        directory = self.stage("01-accepted")
        self.verified(directory, role=self.initial_role)
        self.plan(directory, "no-op")
        self.record(directory)
        if phase in ("ui-promote", "ui-demote"):
            self.role("02-role-update", "admin" if phase == "ui-promote" else "member")
            return
        if phase == "final-remove":
            directory = self.stage("02-remove-active-member")
            self.set_members({})
            self.apply(directory, "delete")
            self.verified(directory, state="absent", role=None)
            raw, _ = self.tf(["state", "list"], directory, "state-list")
            require(not raw.strip(), "Resources remain in state")
            self.plan(self.stage("03-final-converged"), "no-op")
            self.record(directory)
            return
        self.role("02-promote", "admin")
        self.role("03-demote", "member")
        directory = self.stage("04-external-role-drift")
        self.drift(directory, "PUT", "admin")
        self.verified(directory, role="admin")
        self.plan(directory, "update")
        self.record(directory)
        directory = self.stage("05-refresh-only")
        before, _ = self.tf(["state", "pull"], directory, "before-state")
        require(json.loads(before)["resources"][0]["instances"][0]["attributes"]["role"] == "member",
                "Normal plan unexpectedly changed persistent role")
        self.apply(directory, "update", refresh=True)
        self.verified(directory, role="admin")
        after, _ = self.tf(["state", "pull"], directory, "after-state")
        require(json.loads(after)["resources"][0]["instances"][0]["attributes"]["role"] == "admin",
                "Refresh-only did not update persistent state")
        self.record(directory)
        directory = self.stage("06-reconcile-role")
        self.apply(directory, "update")
        self.verified(directory)
        self.plan(self.stage("06-reconcile-converged"), "no-op")
        self.record(directory)
        directory = self.stage("07-forget-and-import")
        self.tf(["state", "pull"], directory, "backup-state")
        self.tf(["state", "rm", ADDRESS], directory, "state-rm")
        self.verified(directory)
        self.plan(directory, "create")
        self.tf(["import", "-input=false", "-no-color", ADDRESS, MEMBERSHIP_ID], directory, "import")
        self.tf(["show", "-json"], directory, "state-json")
        self.plan(self.stage("07-import-converged"), "no-op")
        self.record(directory)
        directory = self.stage("08-external-removal")
        self.drift(directory, "DELETE")
        self.verified(directory, state="absent", role=None)
        self.record(directory)
        directory = self.stage("09-reconcile-removal")
        self.apply(directory, "create")
        self.verified(directory, state="pending")
        self.plan(self.stage("09-pending-converged"), "no-op")
        self.record(directory)
        print("WAITING: test user must accept the new invitation before final-remove", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("roles-and-drift", "final-remove", "ui-promote", "ui-demote"))
    args = parser.parse_args()
    Lifecycle(args.phase).run(args.phase)
