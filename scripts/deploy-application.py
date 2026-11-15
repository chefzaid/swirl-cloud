#!/usr/bin/env python3
"""Deploy an environment-aware application through its own pipeline contract.

  list                                   applications, environments, revisions and health
  deploy --application APP --environment ENV [--ref REF | --release-version X.Y.Z] [--yes]
  waiver set --accepted-by NAME --reason TEXT --review-by YYYY-MM-DD | waiver show | waiver clear

Choices come from Argo CD Applications labelled swirl-cloud/environment that are
registered in infra/deployment-environments. A deployment starts the project's
pipeline with PIPELINE_MODE=full and DEPLOYMENT_ENVIRONMENT; int accepts any
branch, uat and prod accept the default branch (a new release) or an existing
release version. Manual release gates are played only after confirmation. Success
is reported only when the target Argo CD Application is Synced and Healthy at a
new revision. A one-day GitLab token is created for the run and revoked after it.

prod needs a second person (segregation of duties, ISO/IEC 27001 A.5.3): the
merged merge request that brought the deployed commit in must be approved in
GitLab by someone other than its author. An installation with one operator
records a waiver instead, as an accepted risk with a review date at most a year
away. Each prod decision is recorded in infra/swirl-cloud-prod-approvals, which
./install.sh --change-log reads.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts/lib"))
from onboarding_services import HTTP, HTTPFailure, ServiceError, forward  # noqa: E402

WAIVERS = ("infra", "swirl-cloud-waivers", "segregation-of-duties")
APPROVALS = ("infra", "swirl-cloud-prod-approvals", "approvals.json")
KEEP_APPROVALS = 500

LABEL = "swirl-cloud/environment"
# Unique per run: acquiring a token revokes others of the same name, which would
# break a concurrent deployment. Tokens expire after a day if a run is killed.
TOKEN_NAME = "swirl-cloud-deploy-" + secrets.token_hex(4)
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")


def kubectl_json(*args):
    result = subprocess.run(["kubectl", "--request-timeout=20s", *args, "-o", "json"], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ServiceError("Kubernetes API call failed: " + (result.stderr.strip().splitlines() or ["unknown error"])[-1])
    return json.loads(result.stdout)


def registered_environments():
    config = kubectl_json("-n", "infra", "get", "configmap", "deployment-environments")
    return set(json.loads(config["data"]["environments.json"])["environments"])


def applications():
    """Return {app: {"project": path, "environments": {env: argo application}}}."""
    registered = registered_environments()
    found = {}
    for app in kubectl_json("-n", "infra", "get", "applications.argoproj.io")["items"]:
        environment = app["metadata"].get("labels", {}).get(LABEL)
        name = app["metadata"]["name"]
        if environment not in registered or not name.endswith("-" + environment):
            continue
        source = app["spec"].get("source") or (app["spec"].get("sources") or [{}])[0]
        match = re.match(r"https?://[^/]+/(.+?)(?:\.git)?$", source.get("repoURL", ""))
        if not match:
            continue
        entry = found.setdefault(name[:-len(environment) - 1], {"project": match[1], "environments": {}})
        entry["environments"][environment] = app
    return found


def application_state(app):
    status = app.get("status", {})
    return {"sync": status.get("sync", {}).get("status"), "health": status.get("health", {}).get("status"),
            "revision": status.get("sync", {}).get("revision") or "", "urls": status.get("summary", {}).get("externalURLs", [])}


def gitlab_token():
    """Return (token, ephemeral). An operator-supplied GITLAB_ADMIN_TOKEN is used as is."""
    if os.environ.get("GITLAB_ADMIN_TOKEN"):
        return os.environ["GITLAB_ADMIN_TOKEN"], False
    script = ('source "$1"; info() { :; }; fail() { echo "$*" >&2; exit 1; }; '
              'GITLAB_ADMIN_TOKEN_NONINTERACTIVE=true; gitlab_acquire_admin_token; printf "%s" "$GITLAB_ADMIN_TOKEN"')
    result = subprocess.run(["bash", "-c", script, "token", str(ROOT / "scripts/lib/gitlab-admin-token.sh")],
                            capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL,
                            env={**os.environ, "GITLAB_BOOTSTRAP_TOKEN_NAME": TOKEN_NAME})
    if result.returncode or not result.stdout.startswith("glpat-"):
        raise ServiceError("A short-lived GitLab token could not be created; set GITLAB_ADMIN_TOKEN instead")
    return result.stdout, True


def revoke_token():
    script = ('source "$1"; info() { :; }; fail() { :; }; GITLAB_ADMIN_TOKEN_EPHEMERAL=true; '
              'gitlab_revoke_ephemeral_admin_token')
    subprocess.run(["bash", "-c", script, "revoke", str(ROOT / "scripts/lib/gitlab-admin-token.sh")],
                   capture_output=True, timeout=180, stdin=subprocess.DEVNULL,
                   env={**os.environ, "GITLAB_BOOTSTRAP_TOKEN_NAME": TOKEN_NAME})


def confirm(question, assume_yes):
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return False
    return input(question + " [y/N] ").strip().lower() in ("y", "yes")


def wait_for_argo(name, previous, timeout=900):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = application_state(kubectl_json("-n", "infra", "get", "applications.argoproj.io", name))
        if state["revision"] and state["revision"] != previous and state["sync"] == "Synced" and state["health"] == "Healthy":
            return state
        time.sleep(15)
    raise ServiceError(f"Argo CD did not report {name} Synced and Healthy at a new revision within {timeout // 60} minutes")


def configmap_value(location, default):
    namespace, name, key = location
    result = subprocess.run(["kubectl", "--request-timeout=20s", "-n", namespace, "get", "configmap", name, "-o", "json"],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        return default
    return json.loads(json.loads(result.stdout).get("data", {}).get(key) or json.dumps(default))


def save_configmap_value(location, value):
    namespace, name, key = location
    document = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name, "namespace": namespace},
                "data": {key: json.dumps(value, indent=2, sort_keys=True)}}
    result = subprocess.run(["kubectl", "apply", "-f", "-"], input=json.dumps(document), capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ServiceError("Could not record in " + name + ": " + (result.stderr.strip().splitlines() or ["kubectl failed"])[-1])


def release_commit(api, project_id, version):
    """The commit a release version points at, from its tag (1.2.3 or v1.2.3)."""
    for tag in (version, "v" + version):
        try:
            return api.request("GET", f"/projects/{project_id}/repository/tags/{quote(tag, safe='')}")["commit"]
        except HTTPFailure as error:
            if error.status != 404:
                raise
    return None


def change_approval(api, project_id, sha):
    """(merge request, approvers other than its author) for the merged request that brought sha in."""
    merged = [mr for mr in api.request("GET", f"/projects/{project_id}/repository/commits/{sha}/merge_requests")
              if mr.get("state") == "merged"]
    for mr in merged:
        approvals = api.request("GET", f"/projects/{project_id}/merge_requests/{mr['iid']}/approvals")
        author = (mr.get("author") or {}).get("username")
        others = sorted({entry["user"]["username"] for entry in approvals.get("approved_by", [])
                         if (entry.get("user") or {}).get("username") not in (None, author)})
        if others:
            return mr, others
    return (merged[0] if merged else None), []


def production_decision(api, project_id, sha, today):
    """The approval or the valid waiver that lets sha reach prod; refuses otherwise."""
    mr, approvers = change_approval(api, project_id, sha)
    if approvers:
        return {"merge_request": mr.get("web_url", ""), "author": (mr.get("author") or {}).get("username", ""), "approved_by": approvers}
    waiver = configmap_value(WAIVERS, {})
    if waiver and date.fromisoformat(waiver["review_by"]) >= today:
        return {"merge_request": (mr or {}).get("web_url", ""), "author": ((mr or {}).get("author") or {}).get("username", ""),
                "approved_by": [], "waiver": waiver}
    where = f"merge request {mr['web_url']}" if mr else "a merge request (the commit came in without one)"
    expired = f" The waiver's review date {waiver['review_by']} has passed." if waiver else ""
    raise ServiceError(f"prod needs {where} approved in GitLab by someone other than its author.{expired} "
                       "An installation with one operator records a waiver: deploy-application.py waiver set")


def deploy(args):
    apps = applications()
    entry = apps.get(args.application)
    if not entry:
        raise ServiceError("Unknown or not environment-aware application; choose one from `list`")
    if args.environment not in entry["environments"]:
        raise ServiceError(f"{args.application} has no registered {args.environment} environment")
    if args.release_version and not VERSION.fullmatch(args.release_version):
        raise ServiceError("Release versions look like 1.2.3")
    if args.ref and not REF.fullmatch(args.ref):
        raise ServiceError("Invalid branch or tag name")
    argo_name = entry["environments"][args.environment]["metadata"]["name"]
    before = application_state(entry["environments"][args.environment])
    token, ephemeral = gitlab_token()
    try:
        with forward("service/gitlab", 80) as origin:
            api = HTTP(origin + "/api/v4", {"PRIVATE-TOKEN": token})
            project = api.request("GET", "/projects/" + quote(entry["project"], safe=""))
            default_branch = project["default_branch"]
            ref = args.ref or default_branch
            if args.environment != "int" and ref != default_branch:
                raise ServiceError(f"{args.environment} deploys only from {default_branch}; use --release-version to promote a release")
            # One deployment per environment at a time; the app's resource group also serializes jobs.
            for status in ("created", "waiting_for_resource", "preparing", "pending", "running", "manual"):
                for active in api.request("GET", f"/projects/{project['id']}/pipelines?status={status}&per_page=50"):
                    settings = api.request("GET", f"/projects/{project['id']}/pipelines/{active['id']}/variables")
                    if any(v["key"] == "DEPLOYMENT_ENVIRONMENT" and v["value"] == args.environment for v in settings):
                        raise ServiceError(f"A {args.environment} deployment is already {status}: {active['web_url']}")
            commit = api.request("GET", f"/projects/{project['id']}/repository/commits/{quote(ref, safe='')}")
            decision = None
            if args.environment == "prod":
                released = release_commit(api, project["id"], args.release_version) if args.release_version else commit
                if not released:
                    raise ServiceError(f"No tag for release {args.release_version}; prod checks the release's commit for an approval")
                decision = production_decision(api, project["id"], released["id"], datetime.now(timezone.utc).date())
                decision.update(application=args.application, commit=released["id"])
                print("Approved by " + ", ".join(decision["approved_by"]) + f" ({decision['merge_request']})." if decision["approved_by"]
                      else f"No second approval: waiver accepted by {decision['waiver']['accepted_by']}, review by {decision['waiver']['review_by']}.")
            variables = [{"key": "PIPELINE_MODE", "value": "full"}, {"key": "DEPLOYMENT_ENVIRONMENT", "value": args.environment}]
            if args.release_version:
                variables.append({"key": "RELEASE_VERSION", "value": args.release_version})
            what = (f"existing release {args.release_version} (no rebuild)" if args.release_version else
                    f"{ref} at {commit['short_id']} \"{commit['title'][:60]}\" as a "
                    f"{'snapshot' if args.environment == 'int' else 'new release'}")
            print(f"Deploying {args.application} {what} to {args.environment} (currently {before['revision'][:8] or 'none'}, "
                  f"{before['sync']}/{before['health']}).")
            if not confirm("Start the pipeline?", args.yes):
                print("Cancelled.")
                return 1
            pipeline = api.request("POST", f"/projects/{project['id']}/pipeline", {"ref": ref, "variables": variables})
            print(f"Pipeline {pipeline['id']}: {pipeline['web_url']}", flush=True)
            if decision:
                decision.update(at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(), pipeline=pipeline["web_url"],
                                deployed_by=os.environ.get("SUDO_USER") or os.environ.get("USER", ""))
                save_configmap_value(APPROVALS, (configmap_value(APPROVALS, []) + [decision])[-KEEP_APPROVALS:])
            played, seen = set(), {}
            deadline = time.monotonic() + args.timeout * 60
            while time.monotonic() < deadline:
                pipeline = api.request("GET", f"/projects/{project['id']}/pipelines/{pipeline['id']}")
                jobs = api.request("GET", f"/projects/{project['id']}/pipelines/{pipeline['id']}/jobs?per_page=100")
                for job in jobs:
                    if seen.get(job["id"]) != job["status"]:
                        seen[job["id"]] = job["status"]
                        print(f"  {job['stage']:>14} {job['name']}: {job['status']}", flush=True)
                if pipeline["status"] in ("success", "failed", "canceled", "skipped"):
                    break
                for job in jobs:
                    if job["status"] == "manual" and job["id"] not in played and not job.get("allow_failure"):
                        played.add(job["id"])
                        if not confirm(f"Release gate '{job['name']}' is waiting. Run it now?", args.approve_manual):
                            print(f"Left for approval in GitLab: {job['web_url']}")
                            return 2
                        api.request("POST", f"/projects/{project['id']}/jobs/{job['id']}/play")
                time.sleep(10)
            else:
                raise ServiceError(f"The pipeline did not finish within {args.timeout} minutes: {pipeline['web_url']}")
            if pipeline["status"] != "success":
                raise ServiceError(f"The pipeline ended {pipeline['status']}: {pipeline['web_url']}")
    except HTTPFailure as error:
        raise ServiceError(f"GitLab rejected the request (HTTP {error.status}); check the ref and the project's CI contract") from None
    finally:
        if ephemeral:
            revoke_token()
    print(f"Pipeline succeeded; waiting for Argo CD {argo_name}...", flush=True)
    after = wait_for_argo(argo_name, before["revision"])
    print(f"Deployed: {argo_name} is Synced and Healthy at {after['revision'][:8]}."
          + (f" Open {min(after['urls'], key=len)}" if after["urls"] else ""))
    return 0


def waiver_command(args):
    current = configmap_value(WAIVERS, {})
    if args.action == "show":
        print(json.dumps(current, indent=2) if current else "No waiver: prod needs a second approval.")
        return 0
    if args.action == "clear":
        save_configmap_value(WAIVERS, {})
        print("Waiver cleared: prod needs a second approval.")
        return 0
    if not (args.accepted_by and args.reason and args.review_by):
        raise ServiceError("waiver set needs --accepted-by, --reason and --review-by")
    review = date.fromisoformat(args.review_by)
    today = datetime.now(timezone.utc).date()
    if not today < review <= today + timedelta(days=366):
        raise ServiceError("--review-by must be in the next year")
    if len(args.reason.strip()) < 10:
        raise ServiceError("give the reason, such as why the company has one operator")
    save_configmap_value(WAIVERS, {"accepted_by": args.accepted_by, "reason": args.reason.strip(), "review_by": review.isoformat(),
                                   "recorded": datetime.now(timezone.utc).replace(microsecond=0).isoformat()})
    print(f"Waiver recorded until {review}: prod deploys without a second approval, and each one is recorded as waived.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    run = sub.add_parser("deploy")
    run.add_argument("--application", required=True)
    run.add_argument("--environment", required=True, choices=("int", "uat", "prod"))
    source = run.add_mutually_exclusive_group()
    source.add_argument("--ref", help="branch or tag (int); uat/prod use the default branch")
    source.add_argument("--release-version", help="promote an existing release, for example 1.2.3")
    run.add_argument("--yes", action="store_true", help="start without the confirmation question")
    run.add_argument("--approve-manual", action="store_true", help="also run manual release gates without asking")
    run.add_argument("--timeout", type=int, default=60, help="pipeline timeout in minutes (default 60)")
    waiver = sub.add_parser("waiver", help="the single-operator waiver of the second approval for prod")
    waiver.add_argument("action", choices=("set", "show", "clear"))
    waiver.add_argument("--accepted-by")
    waiver.add_argument("--reason")
    waiver.add_argument("--review-by")
    args = parser.parse_args()
    if args.command == "waiver":
        return waiver_command(args)
    if args.command == "list":
        apps = applications()
        if not apps:
            print("No environment-aware applications are registered.")
        for name, entry in sorted(apps.items()):
            print(f"{name}  ({entry['project']})")
            for environment in ("int", "uat", "prod"):
                if environment in entry["environments"]:
                    state = application_state(entry["environments"][environment])
                    print(f"  {environment:5} {state['revision'][:8] or '-':9} {state['sync']}/{state['health']}  "
                          + (min(state["urls"], key=len) if state["urls"] else ""))
        return 0
    return deploy(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ServiceError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
