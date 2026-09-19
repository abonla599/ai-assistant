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
      if (meta) meta.setAttribute("content", next === "dark" ? "#0b0d12" : "#ffffff");
      storeTheme(next);
      syncThemeLabel();
    });
  }

  /* ---------- 轮播 ---------- */
  var carousel = document.querySelector("[data-carousel]");
  if (carousel) {
    var stage = carousel.querySelector(".stage");
    var slides = [].slice.call(carousel.querySelectorAll(".slide"));
    var dotsBox = carousel.querySelector(".dots");
    var index = 0, timer = null, paused = false;

    slides.forEach(function (_, i) {
      var dot = document.createElement("button");
      dot.type = "button";
      dot.className = "dot";
      dot.setAttribute("role", "tab");
      dot.setAttribute("aria-label", "看第 " + (i + 1) + " 张");
      dot.addEventListener("click", function () { go(i); });
      dotsBox.appendChild(dot);
    });
    var dots = [].slice.call(dotsBox.children);

    function go(i) {
      index = (i + slides.length) % slides.length;
      slides.forEach(function (s, n) { s.classList.toggle("is-active", n === index); });
      dots.forEach(function (d, n) { d.setAttribute("aria-selected", n === index ? "true" : "false"); });
      if (stage) stage.style.transform = "translateX(" + (-index * 100) + "%)";
    }

    var prev = carousel.querySelector("#prevBtn");
    var next = carousel.querySelector("#nextBtn");
    if (prev) prev.addEventListener("click", function () { go(index - 1); });
    if (next) next.addEventListener("click", function () { go(index + 1); });

    /* 自动播必须能停：停不下来的轮播对键盘和读屏用户是障碍，不是设计。 */
    var pauseBtn = carousel.querySelector("#pauseBtn");
    function setPaused(v) {
      paused = v;
      if (pauseBtn) {
        pauseBtn.setAttribute("aria-pressed", v ? "true" : "false");
        pauseBtn.setAttribute("aria-label", v ? "继续轮播" : "暂停轮播");
      }
      if (v) { clearInterval(timer); timer = null; }
      else if (!timer && !reduceMotion) { timer = setInterval(function () { go(index + 1); }, 6000); }
    }
    if (pauseBtn) {
      pauseBtn.addEventListener("click", function () { setPaused(!paused); });
    }
    ["mouseenter", "focusin"].forEach(function (ev) {
      carousel.addEventListener(ev, function () { if (!paused) setPaused(true); });
    });
    ["mouseleave", "focusout"].forEach(function (ev) {
      carousel.addEventListener(ev, function () { if (!paused) setPaused(false); });
    });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) setPaused(true);
      else if (!paused) setPaused(false);
    });
    carousel.setAttribute("tabindex", "0");
    carousel.addEventListener("keydown", function (e) {
      if (e.key === "ArrowLeft") { go(index - 1); e.preventDefault(); }
      if (e.key === "ArrowRight") { go(index + 1); e.preventDefault(); }
    });

    /* 触摸横滑：手机上这是最自然的动作，不给它等于装了轮播还不让翻。 */
    var startX = null;
    carousel.addEventListener("touchstart", function (e) {
      startX = e.touches[0].clientX;
    }, { passive: true });
    carousel.addEventListener("touchend", function (e) {
      if (startX === null) return;
      var dx = e.changedTouches[0].clientX - startX;
      if (Math.abs(dx) > 40) go(index + (dx < 0 ? 1 : -1));
      startX = null;
    });

    go(0);
    if (reduceMotion) setPaused(true); else setPaused(false);
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

  /* ---------- 回到顶部 ---------- */
  var topBtn = document.getElementById("topBtn");
  if (topBtn) {
    var onScroll = function () { topBtn.hidden = window.scrollY < 600; };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    topBtn.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
    });
  }
})();
