#!/usr/bin/env python3
"""Guards for repository import (add-repos.sh): names and selections are strict,
the GitLab origin carries no credentials or path, API errors never echo responses
and redirects are never followed with the token, the installed platform's
settings are discovered without guessing, an unmanaged GitHub workflow or a
public destination for a private source stops the import, and one failing
repository does not stop the others."""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("onboard", ROOT / "scripts/onboard-repositories.py")
onboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(onboard)
Error = onboard.ReplicationError
GITHUB = {"REPOSITORY_SOURCE": "github", "GITHUB_USERNAME": "alice", "GITLAB_GROUP_PATH": "acme/apps",
          "GITLAB_PUBLIC_URL": "https://gitlab.example.com/"}


def environment(**values):
    return mock.patch.dict(os.environ, values, clear=True)


class Names(unittest.TestCase):
    def test_names_are_validated_and_deduplicated(self):
        self.assertEqual(["shop", "acme/api"], onboard.names(" shop, acme/api ,shop"))
        for value in ("", "shop,,api", "../etc", "a b", "-x/y", "acme/"):
            with self.subTest(value=value), self.assertRaises(Error):
                onboard.names(value)

    def test_selection(self):
        repositories = ["alice/shop", "alice/api", "bob/tools"]
        self.assertEqual([], onboard.select_repositories(" None ", repositories))
        self.assertEqual(repositories, onboard.select_repositories("ALL", repositories))
        self.assertEqual(["alice/api", "bob/tools"], onboard.select_repositories("api,bob/tools,api", repositories))
        with self.assertRaisesRegex(Error, "'web' is not an imported repository"):
            onboard.select_repositories("web", repositories)
        with self.assertRaisesRegex(Error, "is not an imported repository"):
            onboard.select_repositories("shop", ["alice/shop", "bob/shop"])


class Inputs(unittest.TestCase):
    def test_github_names_take_the_user_as_owner(self):
        with environment(**GITHUB, GITHUB_REPOSITORIES="shop,acme/api,alice/shop"):
            self.assertEqual((["alice/shop", "acme/api"], "acme/apps", "https://gitlab.example.com"), onboard.inputs())

    def test_github_inputs_are_refused(self):
        cases = {"same name from two owners": dict(GITHUB_REPOSITORIES="alice/shop,bob/shop"),
                 "nested owner": dict(GITHUB_REPOSITORIES="a/b/c"),
                 "bad username": dict(GITHUB_USERNAME="alice smith", GITHUB_REPOSITORIES="shop"),
                 "unknown selection": dict(GITHUB_REPOSITORIES="shop", DEPLOY_REPOSITORIES="api")}
        for label, values in cases.items():
            with self.subTest(label), environment(**{**GITHUB, **values}), self.assertRaises(Error):
                onboard.inputs()

    def test_gitlab_projects_must_be_directly_in_the_group(self):
        base = {"REPOSITORY_SOURCE": "gitlab", "GITLAB_GROUP_PATH": "acme/apps", "GITLAB_PUBLIC_URL": "https://gitlab.example.com"}
        with environment(**base, GITLAB_PROJECTS="shop,acme/apps/api"):
            self.assertEqual((["apps/shop", "apps/api"], "acme/apps", "https://gitlab.example.com"), onboard.inputs())
        for projects, message in (("other/api", "directly in the group"), ("acme/apps/sub/api", "directly in the group"),
                                  ("", "nonempty")):
            with self.subTest(projects=projects), environment(**base, GITLAB_PROJECTS=projects), self.assertRaisesRegex(Error, message):
                onboard.inputs()
        with environment(**base), self.assertRaisesRegex(Error, "nonempty"):
            onboard.inputs()

    def test_the_gitlab_origin_is_a_bare_https_origin(self):
        for url in ("http://gitlab.example.com", "https://user:pw@gitlab.example.com", "https://gitlab.example.com/path",
                    "https://gitlab.example.com/?a=1", "https://"):
            with self.subTest(url=url), environment(GITLAB_GROUP_PATH="acme", GITLAB_PUBLIC_URL=url), self.assertRaises(Error):
                onboard.gitlab_target()
        with environment(GITLAB_GROUP_PATH="acme/../x", GITLAB_PUBLIC_URL="https://gitlab.example.com"), self.assertRaises(Error):
            onboard.gitlab_target()


class Api(unittest.TestCase):
    def api(self, outcome):
        api = onboard.API("https://gitlab.example.com/api/v4", {"PRIVATE-TOKEN": "glpat-secret"})
        opener = mock.Mock()
        if isinstance(outcome, Exception):
            opener.open.side_effect = outcome
        else:
            response = mock.MagicMock()
            response.__enter__.return_value = io.BytesIO(outcome)
            opener.open.return_value = response
        api.opener = opener
        return api

    def test_answers_and_missing_resources(self):
        self.assertEqual({"id": 1}, self.api(b'{"id": 1}').call("GET", "projects/1"))
        self.assertIsNone(self.api(b"").call("DELETE", "projects/1"))
        missing = HTTPError("u", 404, "Not Found", {}, io.BytesIO(b"{}"))
        self.assertIsNone(self.api(missing).call("GET", "projects/1", missing=True))

    def test_errors_never_echo_the_response_or_query(self):
        body = io.BytesIO(b'{"message": "token glpat-secret rejected"}')
        with self.assertRaises(Error) as raised:
            self.api(HTTPError("u", 403, "Forbidden", {}, body)).call("GET", "projects?private_token=glpat-secret")
        self.assertEqual("GET projects returned HTTP 403.", str(raised.exception))
        with self.assertRaisesRegex(Error, "Unable to reach"):
            self.api(URLError("down")).call("GET", "user")
        with self.assertRaisesRegex(Error, "non-JSON API response"):
            self.api(b"<html>sign in</html>").call("GET", "user")

    def test_redirects_are_not_followed(self):
        handler = onboard.NoRedirect()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://login.example.com/"))
        self.assertTrue(any(isinstance(h, onboard.NoRedirect) for h in onboard.API("https://x", {}).opener.handlers))


class PlatformContext(unittest.TestCase):
    def discover(self, resources, **env):
        def run(command, **kwargs):
            key = (command[3], command[4])
            found = resources.get(key)
            return mock.Mock(returncode=0, stdout=json.dumps(found) if found else "")
        with environment(**env), mock.patch.object(onboard.subprocess, "run", run):
            return onboard.platform_context(required=True)

    APPLICATION = {"spec": {"source": {"repoURL": "https://gitlab.example.com/acme/swirl-cloud.git", "helm": {
        "values": "publicDomain: example.com\n", "valuesObject": {"internalDnsZone": "internal.example.com", "keycloakRealm": "staff",
                                                                  "gitlabGroupPath": "acme", "gitlabProjectName": "swirl-cloud",
                                                                  "tlsSecretName": "wildcard-tls"}}}}}

    def test_settings_come_from_the_root_application(self):
        context = self.discover({("application", "swirl-cloud"): self.APPLICATION})
        self.assertEqual({"PLATFORM_DOMAIN": "example.com", "INTERNAL_DNS_ZONE": "internal.example.com", "KEYCLOAK_REALM": "staff",
                          "GITLAB_GROUP_PATH": "acme", "TLS_SECRET_NAME": "wildcard-tls",
                          "PLATFORM_SECURITY_PROJECT_PATH": "acme/swirl-cloud/security",
                          "GITLAB_PUBLIC_URL": "https://gitlab.example.com"}, context)

    def test_explicit_settings_win(self):
        context = self.discover({("application", "swirl-cloud"): self.APPLICATION}, PLATFORM_DOMAIN="example.org",
                                GITLAB_PUBLIC_URL="https://code.example.org")
        self.assertEqual(("example.org", "https://code.example.org"), (context["PLATFORM_DOMAIN"], context["GITLAB_PUBLIC_URL"]))

    def test_older_installations_are_read_from_their_resources(self):
        resources = {
            ("configmap", "coredns-custom"): {"data": {"internal.server": "rewrite stop name suffix .internal.example.com. "
                                                                          ".infra.svc.cluster.local. answer auto\n"}},
            ("deployment", "oauth2-proxy"): {"spec": {"template": {"spec": {"containers": [
                {"args": ["--provider=oidc", "--oidc-issuer-url=https://keycloak.example.com/auth/realms/staff"]}]}}}},
            ("ingress", "gitlab-ingress"): {"spec": {"rules": [{"host": "gitlab.example.com"}]}},
        }
        context = self.discover(resources)
        keys = ("PLATFORM_DOMAIN", "INTERNAL_DNS_ZONE", "KEYCLOAK_REALM", "GITLAB_PUBLIC_URL")
        self.assertEqual(("example.com", "internal.example.com", "staff", "https://gitlab.example.com"), tuple(context[k] for k in keys))

    def test_nothing_is_guessed(self):
        with self.assertRaisesRegex(Error, "Unable to discover installed platform settings: .*INTERNAL_DNS_ZONE"):
            self.discover({}, PLATFORM_DOMAIN="example.com", KEYCLOAK_REALM="staff")

    def test_unsafe_settings_are_refused(self):
        for env, message in (({"INTERNAL_DNS_ZONE": "example.com"}, "must differ"), ({"KEYCLOAK_REALM": "master"}, "application realm"),
                             ({"PLATFORM_DOMAIN": "Example..com"}, "must be a DNS zone"),
                             ({"PLATFORM_SECURITY_PROJECT_PATH": "security"}, "registry project path")):
            values = {"PLATFORM_DOMAIN": "example.com", "INTERNAL_DNS_ZONE": "internal.example.com", "KEYCLOAK_REALM": "staff",
                      "GITLAB_PUBLIC_URL": "https://gitlab.example.com", **env}
            with self.subTest(env=env), self.assertRaisesRegex(Error, message):
                self.discover({}, **values)


class FakeAPI:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []
        self.url = "https://gitlab.example.com/api/v4"

    def call(self, method, path, data=None, missing=False):
        self.calls.append((method, path, data))
        for (m, prefix), answer in self.answers.items():
            if m == method and path.startswith(prefix):
                return answer() if callable(answer) else answer
        if missing:
            return None
        raise AssertionError((method, path))


class Import(unittest.TestCase):
    def replicator(self, github, gitlab):
        with environment(**GITHUB, GITHUB_ADMIN_TOKEN="ghp", GITLAB_ADMIN_TOKEN="glpat"):
            replicator = onboard.Replicator("acme/apps", "https://gitlab.example.com")
        replicator.github, replicator.gitlab = FakeAPI(github), FakeAPI(gitlab)
        return replicator

    SOURCE = {"default_branch": "main", "private": True, "permissions": {"admin": True}}

    def test_an_unmanaged_workflow_stops_the_import(self):
        workflow = {"content": base64.b64encode(b"name: Deploy\non: push\n").decode(), "sha": "abc"}
        replicator = self.replicator({("GET", "repos/alice/shop/contents"): workflow, ("GET", "repos/alice/shop"): self.SOURCE},
                                     {("GET", "projects/acme%2Fapps%2Fshop"): {"id": 7, "visibility": "private"}})
        with self.assertRaisesRegex(Error, "unmanaged workflow"):
            replicator.import_repository("alice/shop", 3)
        self.assertNotIn("PUT", [m for m, _, _ in replicator.github.calls])

    def test_a_private_source_needs_a_private_destination(self):
        replicator = self.replicator({("GET", "repos/alice/shop"): self.SOURCE},
                                     {("GET", "projects/acme%2Fapps%2Fshop"): {"id": 7, "visibility": "internal"}})
        with self.assertRaisesRegex(Error, "private GitLab destination"):
            replicator.import_repository("alice/shop", 3)

    def test_the_source_must_be_writable_and_active(self):
        for source in ({**self.SOURCE, "archived": True}, {**self.SOURCE, "permissions": {"push": True}}):
            replicator = self.replicator({("GET", "repos/alice/shop"): source}, {})
            with self.subTest(source=source), self.assertRaisesRegex(Error, "Administration permission"):
                replicator.import_repository("alice/shop", 3)

    def test_a_new_destination_is_private_with_ci_off_until_synchronized(self):
        projects = iter([{"id": 7, "visibility": "private", "default_branch": "main"}])
        replicator = self.replicator({("GET", "repos/alice/shop/contents"): None, ("GET", "repos/alice/shop"): self.SOURCE,
                                      ("PUT", "repos/alice/shop"): {}},
                                     {("POST", "projects"): {"id": 7, "visibility": "private"},
                                      ("GET", "projects/7"): lambda: next(projects)})
        with mock.patch.object(onboard.subprocess, "run") as sync:
            replicator.import_repository("alice/shop", 3)
        created = next(data for method, path, data in replicator.gitlab.calls if method == "POST")
        self.assertEqual(("private", "disabled", 3), (created["visibility"], created["builds_access_level"], created["namespace_id"]))
        put = next(data for method, path, data in replicator.github.calls if method == "PUT" and path.endswith("sync-gitlab.yml"))
        self.assertEqual((ROOT / onboard.WORKFLOW).read_bytes(), base64.b64decode(put["content"]))
        self.assertIn("[skip ci]", put["message"])
        env = sync.call_args.kwargs["env"]
        self.assertEqual(("alice", "shop", "acme/apps/shop", "true"),
                         (env["GITHUB_OWNER"], env["GITHUB_REPOSITORY"], env["GITLAB_PROJECT_PATH"], env["INITIALIZE_REPOSITORY_SYNC"]))

    def test_existing_gitlab_projects_must_be_ready(self):
        for project, message in ((None, "does not exist"), ({"archived": True}, "archived"), ({"id": 1}, "no default branch")):
            replicator = self.replicator({}, {("GET", "projects/acme%2Fapps%2Fshop"): project} if project else {})
            with self.subTest(message), self.assertRaisesRegex(Error, message):
                replicator.existing_project("apps/shop")


class Replicate(unittest.TestCase):
    def test_one_failing_repository_does_not_stop_the_others(self):
        deployed = []

        class Replicator:
            def __init__(self, group, public_url):
                self.github = None

            def existing_project(self, slug):
                if slug == "apps/broken":
                    raise Error("GitLab project acme/apps/broken does not exist; create and push it first.")
                return {"id": slug}

            def deploy(self, slug, project):
                deployed.append(slug)

        with environment(REPOSITORY_SOURCE="gitlab", DEPLOY_REPOSITORIES="all"), mock.patch.object(onboard, "Replicator", Replicator), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out, mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = onboard.replicate(["apps/shop", "apps/broken", "apps/api"], "acme/apps", "https://gitlab.example.com")
        self.assertEqual(1, code)
        self.assertEqual(["apps/shop", "apps/api"], deployed)
        self.assertIn("[FAILED] apps/broken: GitLab project acme/apps/broken does not exist", err.getvalue())
        self.assertIn("[INFO] Found 2 repository/repositories; 1 need attention.", out.getvalue())

    def test_nothing_imported_is_an_error(self):
        class Replicator:
            def __init__(self, group, public_url):
                self.github = None

            def existing_project(self, slug):
                raise Error("missing")
        with environment(REPOSITORY_SOURCE="gitlab", DEPLOY_REPOSITORIES="none"), mock.patch.object(onboard, "Replicator", Replicator), \
                mock.patch("sys.stdout", new_callable=io.StringIO), mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaisesRegex(Error, "No repositories were successfully imported"):
                onboard.replicate(["apps/shop"], "acme/apps", "https://gitlab.example.com")

    def test_the_github_token_must_belong_to_the_user(self):
        class Replicator:
            def __init__(self, group, public_url):
                self.github = FakeAPI({("GET", "user"): {"login": "mallory"}})
        with environment(**GITHUB), mock.patch.object(onboard, "Replicator", Replicator):
            with self.assertRaisesRegex(Error, "different username"):
                onboard.replicate(["alice/shop"], "acme/apps", "https://gitlab.example.com")


if __name__ == "__main__":
    unittest.main()
