/* PWA service worker：缓存应用外壳，接口请求一律走网络。
 * /v1/* 与 /app 下的 API 绝不被缓存，否则手机会读到上一次的会话与记忆数据。
 *
 * `__ASSET_TOKEN__` 由服务端在 sw.js 出门前替换（见 backend/app/web/web_router.py 的
 * rewritten_html）——这一份内容跟着构建变，缓存名也跟着变，所以下面的 activate 会
 * 把上一版整个清掉。以前那个 `-v9` 是手工 bump 的：忘了 bump 就是"改了但手机上
 * 还是旧的"，而它不报错。
 */
const V = "__ASSET_TOKEN__";
const CACHE = "ai-assistant-shell-" + V;

// 导航那两条不带水印：离线时浏览器发出去的就是 /app/ 这个裸地址，
// 带上 ?v= 的话这份兜底永远匹配不上。它的内容在出门前已经盖好当下水印。
const NAV = ["./", "index.html"];
const ASSETS = [
  "style.css",
  "app.js",
  "api.js",
  "shell.js",
  "layers.js",
  "markdown.js",
  "manifest.webmanifest",
  "icon.png",
  "vendor/marked.min.js",
  "vendor/purify.min.js",
  "vendor/highlight.min.js",
  "vendor/hljs-github-dark.min.css",
];
const SHELL = NAV.concat(ASSETS.map((p) => p + "?v=" + V));

self.addEventListener("install", (evt) => {
  evt.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (evt) => {
  evt.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (evt) => {
  const req = evt.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.pathname.includes("/v1/")) return; // 接口永不缓存
  if (url.origin !== self.location.origin) return;

  // 外壳资源：网络优先，保证改版后立即生效；离线时回退缓存
  evt.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      .catch(() =>
        caches.match(req).then((cached) => cached || caches.match("index.html"))
      )
  );
});
