/* 官网交互：主题、轮播、滚动渐显、导航高亮、复制链接、回到顶部。
   零依赖、零外部请求——脚本里不该出现任何别人的域名：把可用性押在别人的
   uptime 上，等于给自己埋一个"我这边明明是好的"的排障夜。
   语法保守写（var / 函数）：这页要在各种 Android WebView 里跑。 */
(function () {
  "use strict";

  var root = document.documentElement;
  root.classList.add("js");

  var reduceMotion = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* 节流用：滚动和指针追光都走 rAF，每帧最多写一次样式 */
  var raf = window.requestAnimationFrame || function (fn) { return setTimeout(fn, 16); };
  var cancel = window.cancelAnimationFrame || clearTimeout;

  function storeTheme(v) {
    try { localStorage.setItem("site-theme", v); } catch (e) { /* 隐私模式：不记就不记 */ }
  }

  /* ---------- 主题 ---------- */
  var themeBtn = document.getElementById("themeBtn");
  function syncThemeLabel() {
    if (!themeBtn) return;
    var dark = root.getAttribute("data-theme") === "dark";
    themeBtn.setAttribute("aria-label", dark ? "切换到浅色" : "切换到深色");
  }
  if (themeBtn) {
    syncThemeLabel();
    themeBtn.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      var meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute("content", next === "dark" ? "#0b0e15" : "#ffffff");
      storeTheme(next);
      syncThemeLabel();
    });
  }

  /* ---------- 滚动渐显 ---------- */
  var reveals = [].slice.call(document.querySelectorAll(".reveal"));
  function showAll() { reveals.forEach(function (el) { el.classList.add("in"); }); }
  if (reveals.length) {
    if (!("IntersectionObserver" in window) || reduceMotion) {
      showAll();
    } else {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
        });
      }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
      reveals.forEach(function (el) { io.observe(el); });
      /* 兜底：渐显是装饰，内容才是正文。观察器因为任何原因没跑成（视口异常、
         老 WebView、脚本在它之前抛了），都不能让整页停在 opacity:0。
         宁可没有动画，也不能让人看见一张白页。 */
      setTimeout(showAll, 1500);
      window.addEventListener("pageshow", showAll);
    }
  }

  /* ---------- 导航高亮当前区块 ---------- */
  var links = [].slice.call(document.querySelectorAll(".navlinks a"));
  if (links.length && "IntersectionObserver" in window) {
    var byId = {};
    links.forEach(function (a) {
      var id = a.getAttribute("href").slice(1);
      var sec = document.getElementById(id);
      if (sec) byId[id] = a;
    });
    var nav = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        links.forEach(function (a) { a.removeAttribute("aria-current"); });
        var a = byId[en.target.id];
        if (a) a.setAttribute("aria-current", "true");
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    Object.keys(byId).forEach(function (id) { nav.observe(document.getElementById(id)); });
  }

  /* ---------- 复制链接 ---------- */
  var copyBtn = document.getElementById("copyBtn");
  var hint = document.getElementById("copyHint");
  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      var url = location.href;
      function done(ok) {
        if (!hint) return;
        hint.textContent = ok ? "已复制，发给朋友就能打开。" : "复制没成，长按地址栏手动复制。";
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(function () { done(true); }, function () { done(false); });
      } else {
        done(false);
      }
    });
  }

  /* ---------- 滚动联动：导航吸起、回到顶部 ---------- */
  /* 两件事共用一个 rAF 循环：scroll 一次手势能触发上百次，读布局/写样式每帧最多一次，
     否则手机上这几处合成会互相抢主线程，滚起来就是掉帧。 */
  var topbar = document.querySelector(".topbar");
  var topBtn = document.getElementById("topBtn");
  if (topbar || topBtn) {
    var frame = 0;
    var paint = function () {
      frame = 0;
      var box = document.documentElement;
      var y = window.pageYOffset || box.scrollTop || 0;
      if (topbar) topbar.classList.toggle("is-stuck", y > 8);
      if (topBtn) topBtn.hidden = y < 600;
    };
    var onScroll = function () { if (!frame) frame = raf(paint); };
    paint();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll, { passive: true });
    window.addEventListener("pagehide", function () { if (frame) { cancel(frame); frame = 0; } });
    if (topBtn) {
      topBtn.addEventListener("click", function () {
        window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
      });
    }
  }
})();
