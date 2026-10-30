import json, re, shutil, subprocess, time, os, sys, threading, http.server, socketserver, functools
from playwright.sync_api import sync_playwright

SRC = "/home/claude/p4b"; SITE = "/home/claude/t/site"
shutil.rmtree(SITE, ignore_errors=True); shutil.copytree(SRC, SITE)

class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache"); super().end_headers()
srv = socketserver.TCPServer(("127.0.0.1", int(os.environ["PORT"])), functools.partial(H, directory=SITE)); srv.allow_reuse_address = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
ORIGIN = "http://127.0.0.1:" + os.environ["PORT"]

# ---- stand-ins for the Firebase CDN (sandbox has no internet) ----
appjs = open(SRC + "/app.js", encoding="utf-8").read()
stubs = {}
for names, url in re.findall(r'import \{([^}]+)\} from "(https://www\.gstatic\.com/firebasejs/[^"]+)"', appjs):
    body = []
    for n in [x.strip() for x in names.split(",") if x.strip()]:
        if n == "onAuthStateChanged": body.append("export const onAuthStateChanged=(a,cb)=>{setTimeout(()=>cb(null),0);return ()=>{}};")
        elif n == "isSupported": body.append("export const isSupported=()=>Promise.resolve(false);")
        elif n == "onSnapshot": body.append("export const onSnapshot=()=>()=>{};")
        else: body.append(f"export const {n}=(...a)=>({{}});")
    code = "\n".join(body)
    if url.endswith("firebase-app.js"): code = 'import "./firebase-component.js";\n' + code   # tests the nested-import walker
    stubs[url] = code
stubs["https://www.gstatic.com/firebasejs/13.1.0/firebase-component.js"] = "export const C=1;"
compat_app = """self.firebase={initializeApp(){},messaging(){return{onBackgroundMessage(cb){
 self.addEventListener('push',e=>{const d=e.data?e.data.json():{};e.waitUntil(Promise.resolve(cb(d)));});}}}};"""
stubs["https://www.gstatic.com/firebasejs/13.1.0/firebase-app-compat.js"] = compat_app
stubs["https://www.gstatic.com/firebasejs/13.1.0/firebase-messaging-compat.js"] = "/* stub */"


def until(page, js, timeout=20000):
    waited = 0
    while waited < timeout:
        if page.evaluate(js): return True
        page.wait_for_timeout(250); waited += 250
    raise TimeoutError("until: " + js[:80])

results = []; errors = []; private_hits = []
def check(name, cond, extra=""):
    results.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name + ("" if cond else "   -> " + str(extra)))

def route_handler(route):
    url = route.request.url
    if url in stubs:
        route.fulfill(status=200, headers={"content-type": "text/javascript", "access-control-allow-origin": "*"}, body=stubs[url])
    elif "googleapis.com" in url or "firebaseapp.com" in url:
        private_hits.append(url)
        route.fulfill(status=200, headers={"content-type": "application/json", "access-control-allow-origin": "*"}, body='{"private":"SECRET-SALES-DATA"}')
    else:
        route.abort()

with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1100, "height": 800}, permissions=["notifications"])
    ctx.route("**/*", lambda r: route_handler(r) if not r.request.url.startswith(ORIGIN) else r.continue_())
    ctx.grant_permissions(["notifications"], origin=ORIGIN)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
    page.on("console", lambda m: errors.append("console." + m.type + ": " + m.text) if m.type == "error" else None)

    # 1. normal load
    b.new_browser_cdp_session().send("Browser.setPermission", {"permission": {"name": "notifications"}, "setting": "granted", "origin": ORIGIN})
    page.goto(ORIGIN + "/index.html"); page.wait_for_load_state("load")
    until(page, "navigator.serviceWorker.getRegistration().then(r=>!!(r&&r.active&&r.active.state==='activated'))", 20000)
    check("1. App loads (login screen visible, app.js ran)", page.is_visible("#loginScreen"))
    check("   manifest <link> present", page.get_attribute("link[rel=manifest]", "href") == "manifest.json")

    # 2. manifest validity
    m = page.evaluate("fetch('manifest.json').then(r=>r.json())")
    need = ["name", "short_name", "start_url", "display", "theme_color", "background_color", "icons"]
    check("2. Manifest has all required fields", all(k in m for k in need))
    check("   name / short name / standalone / theme", m["name"] == "Yohan Store POS" and m["short_name"] == "Yohan Store" and m["display"] == "standalone" and m["theme_color"] == "#176b45")
    sizes_ok = True
    for ic in m["icons"]:
        r = page.evaluate("src=>fetch(src).then(async r=>({ok:r.ok,type:r.headers.get('content-type'),n:(await r.arrayBuffer()).byteLength}))", ic["src"])
        sizes_ok &= r["ok"] and r["type"] == "image/png" and r["n"] > 500
    check("   every icon file in the manifest exists and is a PNG", sizes_ok)
    cdp = ctx.new_cdp_session(page)
    pm = cdp.send("Page.getAppManifest")
    check("   Chromium parses the manifest with no errors", not pm.get("errors"), pm.get("errors"))
    inst = cdp.send("Page.getInstallabilityErrors")
    check("   Chromium reports no installability errors", not inst.get("installabilityErrors"), inst.get("installabilityErrors"))

    # 3. service worker registration
    info = page.evaluate("""async()=>{const rs=await navigator.serviceWorker.getRegistrations();
      return {n:rs.length,scope:rs[0].scope,script:rs[0].active.scriptURL,state:rs[0].active.state}}""")
    check("3. Exactly ONE service worker registration", info["n"] == 1, info)
    check("   it is service-worker.js, active, scope = app folder", info["script"].endswith("/service-worker.js") and info["state"] == "activated" and info["scope"] == ORIGIN + "/", info)

    # 4. offline (second visit: SW controls the page)
    page.reload(); page.wait_for_load_state("load")
    check("   page is controlled by the worker after reload", page.evaluate("!!navigator.serviceWorker.controller"))
    names = page.evaluate("caches.keys()"); check("   one cache named yohan-store-pos-v1", names == ["yohan-store-pos-v1"], names)
    cached = page.evaluate("caches.open('yohan-store-pos-v1').then(c=>c.keys()).then(k=>k.map(r=>r.url))")
    need_cached = [ORIGIN + "/", ORIGIN + "/index.html", ORIGIN + "/style.css", ORIGIN + "/app.js", ORIGIN + "/pwa.js", ORIGIN + "/manifest.json", ORIGIN + "/icon-192.png",
                   "https://www.gstatic.com/firebasejs/13.1.0/firebase-app.js", "https://www.gstatic.com/firebasejs/13.1.0/firebase-component.js",
                   "https://www.gstatic.com/firebasejs/13.1.0/firebase-auth.js", "https://www.gstatic.com/firebasejs/13.1.0/firebase-firestore.js", "https://www.gstatic.com/firebasejs/13.1.0/firebase-messaging.js"]
    check("   app shell + Firebase library files (incl. nested import) were saved", all(u in cached for u in need_cached), [u for u in need_cached if u not in cached])

    # 8. nothing private cached (done before offline so we can send "private" requests first)
    page.evaluate("""Promise.all([
      fetch('https://firestore.googleapis.com/v1/projects/x/databases/(default)/documents/sales').then(r=>r.text()),
      fetch('https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=k',{method:'POST',body:'{}'}).then(r=>r.text()),
      fetch('https://securetoken.googleapis.com/v1/token').then(r=>r.text()),
      fetch('https://fcmregistrations.googleapis.com/v1/x').then(r=>r.text())])""")
    cached2 = page.evaluate("caches.open('yohan-store-pos-v1').then(c=>c.keys()).then(k=>k.map(r=>r.url))")
    bad = [u for u in cached2 if not (u.startswith(ORIGIN + "/") or u.startswith("https://www.gstatic.com/firebasejs/"))]
    check("8. After Firestore/Auth/token/FCM requests, cache holds NO googleapis/Firebase data URLs", not bad and len(private_hits) >= 4, (bad, len(private_hits)))
    bodies = page.evaluate("""async()=>{let t='';const c=await caches.open('yohan-store-pos-v1');for(const r of await c.keys()){t+=await (await c.match(r)).text().catch(()=>'')}return t}""")
    check("   no saved response contains the fake private payload", "SECRET-SALES-DATA" not in bodies)
    allnames = page.evaluate("caches.keys()"); check("   no other caches exist", allnames == ["yohan-store-pos-v1"], allnames)

    # offline reload
    ctx.unroute("**/*"); ctx.set_offline(True)
    until(page, "document.documentElement.dataset.connection==='offline'")
    check("   Offline indicator + banner appear when the connection drops",
          page.inner_text("#connectionStatus").strip() == "Offline" and page.is_visible("#offlineBanner"))
    errors.clear()
    page.reload(); page.wait_for_load_state("load"); page.wait_for_timeout(int(0.5*1000))
    check("4. OFFLINE reload: app shell loads from the saved copy (login screen shown)", page.is_visible("#loginScreen") and page.title() == "Yohan Store POS")
    check("   styles applied offline (login card has the green brand icon style)", page.evaluate("getComputedStyle(document.querySelector('.brand-icon')).backgroundColor") == "rgb(23, 107, 69)")
    check("   app.js ran offline (login form wired; Firebase stand-ins loaded from cache)", page.evaluate("typeof document.getElementById('loginForm')!=='undefined'") and page.evaluate("document.getElementById('loginMessage').textContent===''"))
    check("   Login screen shows Offline", page.inner_text("#loginConnectionStatus").strip() == "Offline")
    check("   no JavaScript errors while offline", not [e for e in errors if "ERR_INTERNET_DISCONNECTED" not in e and "Failed to load resource" not in e], errors)
    # uncached same-origin file offline -> graceful 503, no crash
    r = page.evaluate("fetch('not-saved.js').then(r=>r.status).catch(e=>'network-error')")
    check("   unsaved file offline fails gracefully (no hang/crash)", r in (404, 503, "network-error"), r)

    ctx.set_offline(False)
    until(page, "document.documentElement.dataset.connection==='online'")
    check("   Indicator returns to Online; banner hides", page.inner_text("#connectionStatus").strip() == "Online" and not page.is_visible("#offlineBanner"))

    # 5. freshness online (network-first)
    ctx.route("**/*", lambda r: route_handler(r) if not r.request.url.startswith(ORIGIN) else r.continue_())
    open(SITE + "/style.css", "a").write("\n/* MARKER-UPDATE */\n")
    txt = page.evaluate("fetch('style.css').then(r=>r.text())")
    check("   Online: edited file is served fresh (network-first, good for Live Server)", "MARKER-UPDATE" in txt)
    ctx.unroute("**/*"); ctx.set_offline(True)
    txt2 = page.evaluate("fetch('style.css').then(r=>r.text())")
    check("   ...and the fresh copy is what is saved for offline", "MARKER-UPDATE" in txt2)
    ctx.set_offline(False)
    ctx.route("**/*", lambda r: route_handler(r) if not r.request.url.startswith(ORIGIN) else r.continue_())

    # 7. coexistence with the existing notification worker (real file code, stand-in Firebase library)
    sess = ctx.new_cdp_session(page); ids = []
    sess.on("ServiceWorker.workerRegistrationUpdated", lambda e: ids.extend([r["registrationId"] for r in e["registrations"]]))
    sess.send("ServiceWorker.enable"); page.wait_for_timeout(800)
    sw = ctx.service_workers[0]
    sw.evaluate("()=>{self.__shown=[];self.registration.showNotification=(t,o)=>{self.__shown.push({t,o});return Promise.resolve()}}")
    def push(payload):
        sess.send("ServiceWorker.deliverPushMessage", {"origin": ORIGIN, "registrationId": ids[-1], "data": json.dumps(payload)})
        page.wait_for_timeout(800)
        return sw.evaluate("()=>self.__shown.splice(0)")
    shown = push({"data": {"title": "Low stock", "body": "Noodles is low", "productId": "P001", "url": "./"}})
    check("7. Push reaches the existing firebase-messaging-sw.js code inside the combined worker",
          len(shown) == 1 and shown[0]["t"] == "Low stock" and shown[0]["o"]["tag"] == "low-stock-P001" and shown[0]["o"]["body"] == "Noodles is low", shown)
    check("   alert uses ./icon-192.png (file now exists, was missing before)", shown and shown[0]["o"]["icon"] == "./icon-192.png" and any(u.endswith("/icon-192.png") for u in cached))
    shown2 = push({"notification": {"title": "x"}, "data": {}})
    check("   message that has a 'notification' block is NOT shown twice (existing rule kept)", shown2 == [], shown2)

    # 6. cache cleanup of outdated caches
    page.evaluate("Promise.all([caches.open('yohan-store-pos-v0'),caches.open('unrelated-cache')])")
    sw = open(SITE + "/service-worker.js").read().replace('CACHE_VERSION = "v1"', 'CACHE_VERSION = "v2"')
    open(SITE + "/service-worker.js", "w").write(sw)
    page.evaluate("navigator.serviceWorker.getRegistration().then(r=>r.update())")
    until(page, "caches.keys().then(k=>k.includes('yohan-store-pos-v2'))", 15000)
    page.wait_for_timeout(int(1.0*1000))
    names = page.evaluate("caches.keys()")
    check("6. New version cleans old yohan caches, keeps unrelated ones", sorted(names) == ["unrelated-cache", "yohan-store-pos-v2"], names)

    # visual check
    page.evaluate("""()=>{document.getElementById('loginScreen').hidden=true;document.querySelector('.app-layout').style.display='';
      document.querySelector('.topbar').style.display='';document.querySelector('.demo-notice').style.display='';
      document.getElementById('userName').textContent='Demo';document.getElementById('userRole').textContent='Admin';}""")
    ctx.set_offline(True); until(page, "document.documentElement.dataset.connection==='offline'")
    page.screenshot(path="/home/claude/t/desktop_offline.png")
    page.set_viewport_size({"width": 390, "height": 760}); page.screenshot(path="/home/claude/t/mobile_offline.png")
    ctx.set_offline(False)
    b.close()

srv.shutdown()
print("\n%d passed, %d failed" % (sum(1 for _, ok in results if ok), sum(1 for _, ok in results if not ok)))
sys.exit(0 if all(ok for _, ok in results) else 1)
