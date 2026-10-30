# Yohan Store POS — Stage 4B, Phase A: Offline App Access (PWA)

Phase A only. Offline sales, offline data and cloud sync are NOT included (Phases B and C).

## 1. The conflict I found, and how I solved it

A browser allows **one service worker per folder ("scope")**. Your notifications code registers
`firebase-messaging-sw.js` for the app's folder, and a PWA worker needs that same folder. If both
were registered separately, whichever registered last would kick the other out, silently breaking
either notifications or offline mode.

**Solution (one small registration change):** a single worker, `service-worker.js`, which loads your
`firebase-messaging-sw.js` with `importScripts`. `firebase-messaging-sw.js` is **byte-for-byte unchanged**;
its push, low-stock alert, tag and click-to-open code runs inside the combined worker exactly as before.
The one change to existing code is in `app.js`: `SW_URL` now points to `service-worker.js` (plus the text of one error
message). Both `app.js` and `pwa.js` register the same address, so there is only one registration and no scope fight.

Safety choice: if `firebase-messaging-sw.js` cannot be loaded, the new worker refuses to install, so a broken update
can never replace a working worker and quietly switch notifications off.

I also found `firebase-messaging-sw.js` points at `./icon-192.png`, which did not exist (alerts had no icon). It exists now.

## 2. Files

**Changed:** `index.html` (additions only), `style.css` (additions only), `app.js` (2 lines, see above)
**New:** `manifest.json`, `service-worker.js`, `pwa.js`, `icon-192.png`, `icon-512.png`, `icon-maskable-512.png`, `apple-touch-icon.png`
**Unchanged (confirmed byte-identical):** `firestore.rules`, `firebase-messaging-sw.js`
No Firebase config, rules, permissions, HTML IDs or function names were changed or removed.

## 3. What each new piece does (simple version)

- **manifest.json** — lets the browser install the app: name, green theme (#176b45), standalone window, icons.
- **service-worker.js** — saves the app's own files and the public Firebase library code after your first online visit.
  When online it always fetches the newest file (so edits show up and Live Server keeps working); offline it uses the saved copy.
- **What is NEVER saved:** it only touches an exact allow-list: your own app files + public library code from
  `gstatic.com/firebasejs/`. Firestore, Firebase Auth, tokens, push registration and every other address are never
  intercepted, so sales, costs, users, products and logins can never enter the cache. Only GET requests are handled.
- **Cache version:** `CACHE_VERSION` (currently `v1`). Old `yohan-store-pos-*` caches are deleted on update; other caches are left alone.
  Bump it when you add/remove files in the lists at the top of `service-worker.js`.
- **pwa.js** — registers the worker and updates the **Online / Offline** badge (top bar and login screen).
  When offline a red notice explains that sales, restocking, requests and consumption need internet.
  It uses the browser's connection signal, which can say "Online" on a Wi-Fi with no real internet.

## 4. Testing status — honest breakdown

**Performed in a real browser (Chromium 141 via automation), with a local web server and the Firebase CDN files replaced
by stand-ins (the sandbox has no internet).** 29 checks, all passed:
manifest valid and parsed by Chromium; no installability errors; all icons exist; exactly one service worker registration
(service-worker.js, active, scope = app folder); app shell + library files (including a nested import) saved;
offline reload loads the login screen with styles and scripts and no JS errors; Online/Offline badge and notice switch both ways;
a failed offline request does not crash; online edits are served fresh; fake Firestore/Auth/token/FCM responses were never cached
(cache holds only your files and library files); old cache versions are cleaned and unrelated caches kept;
a simulated push reaches the existing `firebase-messaging-sw.js` code inside the combined worker and produces the same low-stock alert
(title, body, `low-stock-<id>` tag, icon), and a message that already has a notification block is not shown twice.
Screenshots of the badge/notice were checked at desktop and phone width.

**Automated/simulated, not the real thing:** the Firebase libraries were stand-ins, so real Firebase Auth, Firestore, Messaging
and the real Firebase library file structure were **not** exercised. The server was a plain static server, not VS Code Live Server.
Notification display was checked by intercepting `showNotification` (headless Chromium always denies notification permission).

**NOT tested (needs you):** VS Code Live Server itself; sign-in and every Firestore operation with your real project
(the only code touched in `app.js` is the worker address); a real FCM push to your phone; whether devices that already enabled
notifications keep receiving them after the worker swap; installing on Android/iPhone; Safari/Firefox.
I did not claim the full existing app (cashier, restock, requests, consumption, reports) was retested; those code paths were not edited.

## 5. Step-by-step testing with VS Code Live Server

1. Back up your project folder. Copy ALL files from this package into it (keep `firestore.rules` and `firebase-messaging-sw.js` as they are).
2. Right-click `index.html` → **Open with Live Server** (use Chrome or Edge; address like `http://127.0.0.1:5500/`).
3. Press **F12 → Application** tab:
   - **Manifest**: shows "Yohan Store POS", green theme, icons, no errors.
   - **Service workers**: `service-worker.js` is "activated and running" (only one entry).
   - **Cache storage**: `yohan-store-pos-v1` lists your files and `gstatic.com/firebasejs/...` files, and NOTHING from `googleapis.com`.
4. **Reload the page once** while online (so the worker controls it).
5. Sign in and use the app normally online: sell something, restock, check reports.
6. Tick **Application → Service workers → Offline** (or turn off Wi-Fi). The badge turns red "Offline" and the notice appears.
   Reload: the app screen should still open. (Sign-in and data need internet — that is Phase B/C.)
7. Untick Offline: the badge returns to green "Online".
8. Notifications: log in as Admin/Staff, click 🔔 Enable Notifications (click it again even if already enabled on this device),
   then trigger a low-stock alert the way you did in Stage 3/4 and confirm it arrives and tapping it opens the POS.
9. Install: the install icon in the address bar (or phone menu → "Install app" / "Add to Home Screen").

Live Server tips: if you edit `service-worker.js` and nothing changes, tick "Update on reload" in Application → Service workers, or
"Clear site data". Live Server's own live-reload script can print harmless errors in the console when offline.
Phone testing needs HTTPS (your hosted site); `http://192.168.x.x` addresses cannot register service workers.

## 6. If something goes wrong

Delete `service-worker.js`, `pwa.js`, `manifest.json`, and restore your old `app.js`, `index.html`, `style.css`.
In the browser: Application → Service workers → Unregister, then Clear site data. No Firestore data is touched by this phase.
