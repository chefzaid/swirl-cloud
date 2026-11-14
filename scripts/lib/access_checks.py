"""Access policy checks for validate-manifests.py (ADR 0031): tools behind OAuth2
Proxy are refused by default, and every access group a manifest names exists."""
import re
from urllib.parse import parse_qs, urlparse

from manifest_checks import require

ADMINISTRATORS = "platform-admins"


def access_groups(resources):
    """Every address that asks OAuth2 Proxy for a decision names its groups: administrators, plus groups the reconciler creates."""
    script = next(doc["data"]["reconcile.sh"] for doc in resources
                  if doc["kind"] == "ConfigMap" and doc["metadata"]["name"] == "keycloak-sso-reconciler")
    created = re.search(r"^\s*for access_group in ([a-z0-9 -]+); do$", script, re.M)
    require(created, "keycloak-sso-reconciler: no access group loop found; update the access check")
    known = set(created[1].split()) | {ADMINISTRATORS}
    require(all(name == ADMINISTRATORS or name.startswith("access-") for name in known),
            "keycloak-sso-reconciler: access groups are named access-<service>")

    def admitted(address, label):
        groups = set(",".join(parse_qs(urlparse(address).query).get("allowed_groups", [])).split(",")) - {""}
        require(ADMINISTRATORS in groups, f"{label}: the address must name allowed_groups including {ADMINISTRATORS}")
        require(groups <= known, f"{label}: groups the reconciler does not create: {', '.join(sorted(groups - known))}")

    middlewares = set()
    for doc in resources:
        name = doc["metadata"].get("name")
        if doc["kind"] == "Middleware" and "oauth2-proxy" in (doc["spec"].get("forwardAuth") or {}).get("address", ""):
            admitted(doc["spec"]["forwardAuth"]["address"], f"Middleware/{name}")
            middlewares.add(f"{doc['metadata']['namespace']}-{name}@kubernetescrd")
        elif doc["kind"] == "Deployment" and name == "oauth2-proxy":
            arguments = doc["spec"]["template"]["spec"]["containers"][0]["args"]
            require(not any(argument.startswith("--allowed-group") for argument in arguments),
                    "oauth2-proxy: a global --allowed-group would refuse every other access group")
        elif doc["kind"] == "ConfigMap":
            for key, content in (doc.get("data") or {}).items():
                for address in re.findall(r"http://oauth2-proxy[^\s;\"']*/oauth2/auth[^\s;\"']*", str(content)):
                    admitted(address, f"ConfigMap/{name}/{key}")
    require(middlewares, "no OAuth2 Proxy middleware found; update the access check")
    for doc in resources:
        if doc["kind"] != "Ingress":
            continue
        chain = (doc["metadata"].get("annotations") or {}).get("traefik.ingress.kubernetes.io/router.middlewares", "")
        used = [name for name in chain.split(",") if re.match(r"infra-platform-(sso|access-)", name)]
        require(set(used) <= middlewares, f"Ingress/{doc['metadata']['name']}: unknown sign-in middleware {used}")
        require(len(used) <= 1, f"Ingress/{doc['metadata']['name']}: one sign-in middleware decides access, not {used}")
