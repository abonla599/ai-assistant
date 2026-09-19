/* PWA service worker：缓存应用外壳，接口请求一律走网络。
 * /v1/* 与 /app 下的 API 绝不被缓存，否则手机会读到上一次的会话与记忆数据。
 */
const CACHE = "ai-assistant-shell-v8";
const SHELL = [
  "./",
  "index.html",
  "style.css",
  "app.js",
  "api.js",
  "shell.js",
  "markdown.js",
  "manifest.webmanifest",
  "icon.png",
  "vendor/marked.min.js",
  "vendor/purify.min.js",
  "vendor/highlight.min.js",
  "vendor/hljs-github-dark.min.css",
];

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
