/**
 * Apply the live Discord invite from GET /discord.json.
 *
 * The game writes that sidecar when staff set ``discord invite`` (login splash).
 * HTML hrefs are the fallback until this fetch lands, or when the file is missing.
 */
(function () {
  "use strict";

  function hostLabel(url) {
    return String(url || "").replace(/^https?:\/\//i, "");
  }

  function apply(url) {
    var nodes = document.querySelectorAll("[data-discord-invite]");
    var i;
    if (!url) {
      for (i = 0; i < nodes.length; i += 1) {
        nodes[i].hidden = true;
      }
      return;
    }
    for (i = 0; i < nodes.length; i += 1) {
      nodes[i].hidden = false;
      nodes[i].setAttribute("href", url);
      if (nodes[i].getAttribute("data-discord-label") === "host") {
        nodes[i].textContent = hostLabel(url);
      }
    }
  }

  fetch("/discord.json", { cache: "no-store" })
    .then(function (res) {
      return res.ok ? res.json() : null;
    })
    .then(function (data) {
      if (!data || !Object.prototype.hasOwnProperty.call(data, "url")) {
        return;
      }
      apply(data.url);
    })
    .catch(function () {
      /* keep HTML fallback */
    });
})();
