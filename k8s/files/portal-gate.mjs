// Portal gate (ADR 0031). It sits in front of Homepage in the same pod.
// Administrators get Homepage unchanged. Everyone else gets a page built here
// from Homepage's own service list, holding only what their access groups open,
// plus the services they could ask for, greyed. Nothing else about the
// platform reaches their browser. Rules: /gate/access.json (portal-access.json).
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import http from "node:http";

const UPSTREAM = { host: process.env.UPSTREAM_HOST || "127.0.0.1", port: Number(process.env.UPSTREAM_PORT || 3000) };
const PORT = Number(process.env.GATE_PORT || 3001);
const ADMINISTRATORS = "platform-admins";
const rules = JSON.parse(readFileSync(process.env.GATE_RULES || "/gate/access.json", "utf8"));
const title = process.env.PORTAL_TITLE || "Cloud";
const contact = process.env.ACCESS_CONTACT || "";
const accountUrl = process.env.ACCOUNT_URL || "";
const signOutUrl = process.env.SIGN_OUT_URL || "";
// What a person who is not an administrator may fetch besides the page itself.
const ASSETS = /^\/(icons\/[\w.-]+|api\/config\/custom\.css)$/;

const TEXT = {
  en: { search: "Search apps and services", none: "Nothing matches.", locked: "Ask for access", account: "Account",
        signOut: "Sign out", empty: "You have no service yet. Ask your administrator for access.",
        subject: "Access request", body: "Please give me access to", unavailable: "The portal is not available right now." },
  fr: { search: "Rechercher une application ou un service", none: "Aucun résultat.", locked: "Demander l'accès", account: "Compte",
        signOut: "Se déconnecter", empty: "Vous n'avez encore aucun service. Demandez un accès à votre administrateur.",
        subject: "Demande d'accès", body: "Merci de me donner accès à", unavailable: "Le portail est indisponible pour le moment." },
};

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function upstream(path, headers = {}) {
  return new Promise((resolve, reject) => {
    const request = http.request({ ...UPSTREAM, path, method: "GET", headers, timeout: 10000 }, (response) => {
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => resolve({ status: response.statusCode, headers: response.headers, body: Buffer.concat(chunks) }));
    });
    request.on("timeout", () => request.destroy(new Error("upstream timeout")));
    request.on("error", reject);
    request.end();
  });
}

// Administrators: the request goes to Homepage as it came.
function passThrough(req, res) {
  const proxied = http.request({ ...UPSTREAM, path: req.url, method: req.method, headers: req.headers }, (response) => {
    res.writeHead(response.statusCode, response.headers);
    response.pipe(res);
  });
  proxied.on("error", () => { if (!res.headersSent) res.writeHead(502); res.end(); });
  req.pipe(proxied);
}

// A service is open to everyone signed in, open or greyed through its access
// group, or hidden. A whole group of applications can need a group too.
function classify(service, groupName, groups) {
  if (rules.applicationGroups.includes(groupName) || rules.open.includes(service.name)) return "open";
  if (rules.groupAccess[groupName]) return groups.includes(rules.groupAccess[groupName]) ? "open" : null;
  const needed = rules.groups[service.name];
  if (!needed) return null;
  return groups.includes(needed) ? "open" : "locked";
}

// Icons as Homepage resolves them: a local file, a Material Design icon drawn as
// a mask (its rule goes in the page's style block, since the page allows no
// inline style attribute), or a dashboard icon.
const mdiName = (name) => (/^mdi-[a-z0-9-]+$/.test(name || "") ? name.slice(4) : null);

function icon(name) {
  if (!name) return "";
  if (name.startsWith("/")) return `<img src="${escape(name)}" alt="" width="24" height="24">`;
  if (mdiName(name)) return `<span class="mdi mdi-${mdiName(name)}"></span>`;
  if (/^[a-z0-9-]+\.(png|svg|webp)$/.test(name)) {
    return `<img src="https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/${name.split(".").pop()}/${name}" alt="" width="24" height="24">`;
  }
  return "";
}

function page(sections, person, text, language, nonce) {
  const cards = (section) => section.services.map((service) => {
    const locked = service.state === "locked";
    const href = locked
      ? `mailto:${encodeURIComponent(contact)}?subject=${encodeURIComponent(`${text.subject}: ${service.name}`)}&body=${encodeURIComponent(`${text.body} ${service.name}. (${person})`)}`
      : service.href;
    return `<li><a class="card${locked ? " locked" : ""}" href="${escape(href)}"${locked ? "" : ' target="_blank" rel="noreferrer"'}>`
      + `<span class="tile">${icon(service.icon)}</span><span class="what"><span class="name">${escape(service.name)}</span>`
      + `<span class="about">${escape(service.description)}</span>${locked ? `<span class="badge">${escape(text.locked)}</span>` : ""}</span></a></li>`;
  }).join("");
  const masks = [...new Set(sections.flatMap((section) => section.services.map((service) => mdiName(service.icon))).filter(Boolean))]
    .map((name) => {
      const source = `https://cdn.jsdelivr.net/npm/@mdi/svg@latest/svg/${name}.svg`;
      return `.mdi-${name}{mask-image:url(${source});-webkit-mask-image:url(${source})}`;
    }).join("");
  const body = sections.length
    ? sections.map((section) => `<section><h2>${escape(section.name)}</h2><ul>${cards(section)}</ul></section>`).join("")
    : `<p class="empty">${escape(text.empty)}</p>`;
  return `<!DOCTYPE html><html lang="${language}" class="dark theme-purple"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>${escape(title)}</title>
<link rel="icon" href="/icons/swirl.svg"><link rel="stylesheet" href="/api/config/custom.css">
<style nonce="${nonce}">
*{box-sizing:border-box}body{margin:0;font:400 15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 20px 56px}
header{display:flex;flex-wrap:wrap;align-items:center;gap:16px 24px;padding:12px 20px;border:1px solid var(--swirl-line);border-radius:14px;background:var(--swirl-panel);box-shadow:var(--swirl-shadow)}
.brand{display:flex;align-items:center;gap:8px;font-size:24px;font-weight:700;letter-spacing:-.04em;color:var(--swirl-text)}.brand i{font-style:normal;color:var(--swirl-orange)}
.find{flex:1 1 260px;max-width:440px;height:36px;padding:0 14px;border:1px solid var(--swirl-line-strong);border-radius:8px;background:var(--swirl-card);color:var(--swirl-text);font:inherit;font-size:13px}
.find::placeholder{color:var(--swirl-subtle)}.find:focus{border-color:var(--swirl-plum);outline:none}
.who{margin-left:auto;display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center;font-size:13px;color:var(--swirl-muted)}
.who a{color:var(--swirl-plum-light);text-decoration:none;text-underline-offset:3px}.who a:hover{text-decoration:underline}
section{margin-top:20px;padding:16px 20px 12px;border:1px solid var(--swirl-line);border-radius:14px;background:var(--swirl-panel);box-shadow:var(--swirl-shadow)}
h2{margin:0 0 14px;font-size:12px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--swirl-muted)}
ul{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.card{position:relative;display:flex;align-items:center;gap:14px;height:100%;padding:12px;border:1px solid var(--swirl-line-strong);border-radius:10px;background:var(--swirl-card);color:var(--swirl-text);text-decoration:none;transition:border-color .12s ease,background-color .12s ease}
.card:hover{border-color:var(--swirl-plum);background:var(--swirl-card-hover)}
.tile{flex:none;display:flex;align-items:center;justify-content:center;width:44px;height:44px;border:1px solid var(--swirl-line-strong);border-radius:10px;background:var(--swirl-tile)}
.tile img{width:24px;height:24px;object-fit:contain}.mdi{width:24px;height:24px;background:var(--swirl-plum-light);mask-repeat:no-repeat;mask-position:center;mask-size:contain;-webkit-mask-repeat:no-repeat;-webkit-mask-position:center;-webkit-mask-size:contain}
.what{display:flex;flex-direction:column;min-width:0}.name{font-size:14px;font-weight:600;line-height:1.3}.about{margin-top:3px;font-size:12px;line-height:1.4;color:var(--swirl-muted)}
.locked{border-style:dashed;background:transparent}.locked .tile,.locked .name,.locked .about{opacity:.5}
.badge{align-self:flex-start;margin-top:8px;padding:2px 8px;border-radius:999px;background:color-mix(in srgb,var(--swirl-orange) 16%,transparent);color:var(--swirl-orange);font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;white-space:nowrap}
.empty,.none{margin:28px 4px;color:var(--swirl-muted)}.none{display:none}[hidden]{display:none!important}
${masks}</style></head><body><main>
<header><span class="brand"><img src="/icons/swirl.svg" alt="" width="34" height="34"><span>Swirl<i>.</i></span></span>
<input class="find" type="search" placeholder="${escape(text.search)}" aria-label="${escape(text.search)}" autocomplete="off">
<span class="who"><span>${escape(person)}</span>${accountUrl ? `<a href="${escape(accountUrl)}">${escape(text.account)}</a>` : ""}${signOutUrl ? `<a href="${escape(signOutUrl)}">${escape(text.signOut)}</a>` : ""}</span></header>
${body}<p class="none">${escape(text.none)}</p></main>
<script nonce="${nonce}">
(() => {
  const find = document.querySelector(".find"), none = document.querySelector(".none");
  find.addEventListener("input", () => {
    const wanted = find.value.trim().toLowerCase();
    let shown = 0;
    for (const section of document.querySelectorAll("section")) {
      let visible = 0;
      for (const item of section.querySelectorAll("li")) {
        const match = !wanted || item.textContent.toLowerCase().includes(wanted);
        item.hidden = !match;
        visible += match;
      }
      section.hidden = !visible;
      shown += visible;
    }
    none.style.display = wanted && !shown ? "block" : "none";
  });
  document.addEventListener("keydown", (event) => {
    if (event.target === find) { if (event.key === "Escape") { find.value = ""; find.dispatchEvent(new Event("input")); find.blur(); } return; }
    if (event.key === "/" || (event.key.toLowerCase() === "k" && (event.ctrlKey || event.metaKey))) { event.preventDefault(); find.focus(); }
  });
})();
</script></body></html>`;
}

async function staff(req, res, groups) {
  const language = /^fr\b/i.test(req.headers["accept-language"] || "") ? "fr" : "en";
  const text = TEXT[language];
  const refuse = (status, message) => {
    res.writeHead(status, { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "private, no-store" });
    res.end(message);
  };
  if (req.method !== "GET" && req.method !== "HEAD") return refuse(405, "Method not allowed");
  try {
    if (ASSETS.test(req.url)) {
      const asset = await upstream(req.url, { host: req.headers.host || "" });
      res.writeHead(asset.status, { "Content-Type": asset.headers["content-type"] || "application/octet-stream",
                                    "Cache-Control": asset.headers["cache-control"] || "private, max-age=300" });
      return res.end(req.method === "HEAD" ? undefined : asset.body);
    }
    if (req.url.split("?")[0] !== "/") return refuse(404, "Not found");
    const services = await upstream("/api/services", { host: req.headers.host || "" });
    if (services.status !== 200) throw new Error(`Homepage answered ${services.status}`);
    const sections = JSON.parse(services.body.toString("utf8")).map((group) => ({
      name: group.name,
      services: (group.services || []).map((service) => ({ ...service, state: classify(service, group.name, groups) }))
        .filter((service) => service.state && service.href)
        .sort((a, b) => (a.state === "locked") - (b.state === "locked")),
    })).filter((section) => section.services.length);
    const nonce = randomBytes(16).toString("base64");
    const person = req.headers["x-auth-request-email"] || req.headers["x-auth-request-user"] || "";
    res.writeHead(200, {
      "Content-Type": "text/html; charset=utf-8", "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
      "Content-Security-Policy": `default-src 'none'; img-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'nonce-${nonce}'; `
        + `script-src 'nonce-${nonce}'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`,
    });
    res.end(req.method === "HEAD" ? undefined : page(sections, person, text, language, nonce));
  } catch (error) {
    console.error(`portal gate: ${error.message}`);
    refuse(502, text.unavailable);
  }
}

http.createServer((req, res) => {
  if (req.url === "/gate/healthz") { res.writeHead(200, { "Content-Type": "text/plain" }); return res.end("ok\n"); }
  const groups = String(req.headers["x-auth-request-groups"] || "").split(",").map((group) => group.trim()).filter(Boolean);
  if (groups.includes(ADMINISTRATORS)) return passThrough(req, res);
  return staff(req, res, groups);
}).listen(PORT, () => console.log(`portal gate listening on ${PORT}`));
