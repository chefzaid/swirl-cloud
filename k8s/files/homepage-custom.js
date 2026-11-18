// The header's search field. It opens Homepage's Quick Launch, which searches
// the portal's apps and services by name and description (settings.yaml
// quicklaunch), not the web. Homepage opens Quick Launch when someone types on
// the page; this field does the same on a click or tap, and "/" or Ctrl+K
// (Cmd+K) open it from the keyboard. Styled in custom.css. It also shows the
// gate's notices and each tile's running version.
(() => {
  // "fr" for portalLanguage fr (templates/resources.yaml, scripts/render-identity.py).
  const language = "en";
  const label = language === "fr" ? "Rechercher une application ou un service" : "Search apps and services";

  // Homepage's own handler opens Quick Launch on a space typed on the page body.
  function openQuickLaunch() {
    document.body.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true, cancelable: true }));
  }

  function field() {
    const wrapper = document.createElement("div");
    wrapper.id = "swirl-search";
    wrapper.className = "information-widget-search";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "swirl-search-field";
    button.setAttribute("aria-haspopup", "dialog");
    button.innerHTML =
      '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"' +
      ' stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>' +
      '<span class="swirl-search-label"></span><kbd aria-hidden="true">/</kbd>';
    button.querySelector(".swirl-search-label").textContent = label;
    button.addEventListener("click", openQuickLaunch);
    wrapper.append(button);
    return wrapper;
  }

  // The header renders once Homepage has loaded its widgets, and again when they change.
  function place() {
    const right = document.getElementById("information-widgets-right");
    if (right && !document.getElementById("swirl-search")) right.prepend(field());
    const input = document.querySelector('[role="dialog"] dialog input');
    if (input && input.placeholder !== label) input.placeholder = label;
  }

  // Each tile's running version, from the portal gate (administrators only), which
  // reads it from the tile's pods: an image update shows without a portal change.
  // Tiles are told apart by their group, as one application has a tile per environment.
  let versions = {};
  function showVersions() {
    for (const group of document.querySelectorAll(".services-group")) {
      const known = versions[group.querySelector(".service-group-name")?.textContent?.trim()] || {};
      for (const tile of group.querySelectorAll("li.service[data-name]")) {
        const name = tile.querySelector(".service-name");
        const version = known[tile.dataset.name];
        let badge = name?.querySelector(".swirl-version");
        if (!name || !version) { badge?.remove(); continue; }
        if (!badge) {
          badge = document.createElement("span");
          badge.className = "swirl-version";
          name.insertBefore(badge, name.querySelector(".service-description"));
        }
        if (badge.textContent !== version) {
          badge.textContent = version;
          badge.title = (language === "fr" ? "Version en service : " : "Running version: ") + version;
        }
      }
    }
  }
  function refreshVersions() {
    fetch("/gate/versions.json", { credentials: "same-origin" }).then((response) => (response.ok ? response.json() : null))
      .then((answer) => { if (answer && typeof answer === "object") { versions = answer; showVersions(); } })
      .catch(() => {});
  }

  new MutationObserver(() => { place(); showVersions(); }).observe(document.body, { childList: true, subtree: true });
  place();
  refreshVersions();
  setInterval(refreshVersions, 300000);

  // Notices of maintenance and incidents (./install.sh --notice), from the portal
  // gate, which answers administrators only; everyone else sees them on the gate's page.
  fetch("/gate/notices.json", { credentials: "same-origin" }).then((response) => (response.ok ? response.json() : []))
    .then((notices) => {
      if (!Array.isArray(notices) || !notices.length || document.getElementById("swirl-notices")) return;
      const list = document.createElement("div");
      list.id = "swirl-notices";
      list.setAttribute("role", "status");
      const when = new Intl.DateTimeFormat(language, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
      for (const notice of notices) {
        const item = document.createElement("p");
        item.className = "swirl-notice" + (Date.parse(notice.starts) <= Date.now() ? " now" : "");
        const service = document.createElement("strong");
        service.textContent = notice.service === "*" ? (language === "fr" ? "Plateforme" : "Platform") : notice.service;
        const time = document.createElement("time");
        time.textContent = `${when.format(new Date(notice.starts))} - ${when.format(new Date(notice.ends))}`;
        const text = document.createElement("span");
        text.textContent = (language === "fr" && notice.fr) || notice.en;
        item.append(service, time, text);
        list.append(item);
      }
      (document.getElementById("inner_wrapper") || document.body).prepend(list);
    })
    .catch(() => {});

  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.target.closest?.("input, textarea, select, [contenteditable]")) return;
    const slash = event.key === "/" && !(event.altKey || event.ctrlKey || event.metaKey);
    const commandK = event.key?.toLowerCase() === "k" && (event.ctrlKey || event.metaKey) && !event.altKey;
    if (slash || commandK) {
      event.preventDefault();
      openQuickLaunch();
    }
  });
})();
