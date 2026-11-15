// Deletes expired checkpoints (scripts/checkpoint.py): every VolumeSnapshot and
// checkpoint record labelled swirl-cloud.io/checkpoint whose expiry annotation
// has passed. Anything without the label is never touched.
import { readFileSync } from "node:fs";

const api = process.env.KUBERNETES_API || "https://kubernetes.default.svc";
const token = readFileSync(process.env.TOKEN_FILE || "/var/run/secrets/kubernetes.io/serviceaccount/token", "utf8").trim();
const label = "swirl-cloud.io/checkpoint";
const expiresKey = "swirl-cloud.io/checkpoint-expires-at";
const now = Date.now();

async function call(method, path) {
  const response = await fetch(api + path, { method, headers: { Authorization: `Bearer ${token}`, Accept: "application/json" } });
  if (!response.ok && !(method === "DELETE" && response.status === 404)) {
    throw new Error(`${method} ${path}: HTTP ${response.status}`);
  }
  return method === "GET" ? response.json() : null;
}

const selector = `?labelSelector=${encodeURIComponent(label)}`;
const lists = [
  ["/apis/snapshot.storage.k8s.io/v1/volumesnapshots", (m) => `/apis/snapshot.storage.k8s.io/v1/namespaces/${m.namespace}/volumesnapshots/${m.name}`],
  ["/api/v1/namespaces/infra/configmaps", (m) => `/api/v1/namespaces/infra/configmaps/${m.name}`],
];
let deleted = 0;
for (const [path, itemPath] of lists) {
  for (const item of (await call("GET", path + selector)).items || []) {
    const expires = Date.parse(item.metadata.annotations?.[expiresKey] || "");
    if (Number.isNaN(expires) || expires > now) continue;
    await call("DELETE", itemPath(item.metadata));
    console.log(`Deleted expired ${item.kind || "object"} ${item.metadata.namespace}/${item.metadata.name} (checkpoint ${item.metadata.labels[label]}).`);
    deleted += 1;
  }
}
console.log(`${deleted} expired checkpoint object(s) deleted.`);
