/* Yohan Store POS - PWA helper (Stage 4B, Phase A)
   1. Shows Online / Offline and keeps it up to date.
   2. Registers the application service worker (service-worker.js).
   Plain script (not a module) so it also runs if app.js fails to load. */
(function () {
  "use strict";

  // ----- Online / Offline indicator -----
  function updateConnectionStatus() {
    var online = navigator.onLine !== false;

    document.querySelectorAll("[data-connection-status]").forEach(function (badge) {
      badge.classList.toggle("is-online", online);
      badge.classList.toggle("is-offline", !online);
      var label = badge.querySelector(".conn-label");
      if (label) label.textContent = online ? "Online" : "Offline";
    });

    var banner = document.getElementById("offlineBanner");
    if (banner) banner.hidden = online;

    document.documentElement.setAttribute("data-connection", online ? "online" : "offline");
  }

  window.addEventListener("online", updateConnectionStatus);
  window.addEventListener("offline", updateConnectionStatus);
  document.addEventListener("DOMContentLoaded", updateConnectionStatus);
  updateConnectionStatus();

  // ----- Service worker -----
  // The page must be on https:// or localhost (Live Server is fine). On file:// there is no service worker.
  if (!("serviceWorker" in navigator) || !window.isSecureContext) return;

  // MUST be the same address app.js uses for notifications (SW_URL), so both share ONE registration.
  var workerUrl = new URL("service-worker.js", window.location.href).href;

  window.addEventListener("load", function () {
    navigator.serviceWorker.register(workerUrl).catch(function (error) {
      console.error("Could not register the app service worker:", error);
    });
  });
})();
