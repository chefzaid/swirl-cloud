// The header's search field. It opens Homepage's Quick Launch, which searches
// the portal's apps and services by name and description (settings.yaml
// quicklaunch), not the web. Homepage opens Quick Launch when someone types on
// the page; this field does the same on a click or tap, and "/" or Ctrl+K
// (Cmd+K) open it from the keyboard. Styled in custom.css.
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

  new MutationObserver(place).observe(document.body, { childList: true, subtree: true });
  place();

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
