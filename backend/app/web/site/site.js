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

    /* 首屏之后把后面三张的 loading 翻成 eager。
       HTML 里那三个 loading="lazy" 是给首屏让路的（四张合计约 500 KB，一起下载就是
       拿三张根本还没露面的图去抢 LCP 的带宽）；但轮播的图横向摆在 overflow:hidden
       的轨道里，浏览器始终不认为它们「快滚进视口」，于是翻到那一张时只剩一个空壳。
       实测：把 loading 改成 eager，2.5 秒内四张全到位，HTML 一个字都不用动。 */
    var shots = [].slice.call(carousel.querySelectorAll(".slide img"));
    function warm(i) {
      var img = shots[i];
      if (img && img.loading !== "eager") img.loading = "eager";
    }
    function warmAll() { for (var k = 0; k < shots.length; k++) warm(k); }
    if (document.readyState === "complete") setTimeout(warmAll, 0);
    else window.addEventListener("load", function () { setTimeout(warmAll, 0); });

    function go(i) {
      index = (i + slides.length) % slides.length;
      slides.forEach(function (s, n) { s.classList.toggle("is-active", n === index); });
      dots.forEach(function (d, n) { d.setAttribute("aria-selected", n === index ? "true" : "false"); });
      if (stage) stage.style.transform = "translateX(" + (-index * 100) + "%)";
      warm(index);   /* 万一 load 那条还没跑（首屏就被人直接翻了页） */
    }

    var prev = carousel.querySelector("#prevBtn");
    var next = carousel.querySelector("#nextBtn");
    if (prev) prev.addEventListener("click", function () { go(index - 1); });
    if (next) next.addEventListener("click", function () { go(index + 1); });

    /* 自动播必须能停：停不下来的轮播对键盘和读屏用户是障碍，不是设计。
       两本账分开记：paused 是用户按了暂停键，held 是指针或焦点正停在这块上、
       或者标签页在后台。合成一个标志位会出这种事故——mouseenter 把 paused 置真,
       mouseleave 那句 `if (!paused)` 就永远不成立,鼠标划过一次轮播,它从此不再自己走。 */
    var pauseBtn = carousel.querySelector("#pauseBtn");
    var held = false;
    function syncTimer() {
      if (paused || held || document.hidden || reduceMotion) {
        clearInterval(timer); timer = null; return;
      }
      if (!timer) timer = setInterval(function () { go(index + 1); }, 6000);
    }
    function setPaused(v) {
      paused = v;
      if (pauseBtn) {
        pauseBtn.setAttribute("aria-pressed", v ? "true" : "false");
        pauseBtn.setAttribute("aria-label", v ? "继续轮播" : "暂停轮播");
      }
      syncTimer();
    }
    if (pauseBtn) {
      pauseBtn.addEventListener("click", function () { setPaused(!paused); });
    }
    ["mouseenter", "focusin"].forEach(function (ev) {
      carousel.addEventListener(ev, function () { held = true; syncTimer(); });
    });
    ["mouseleave", "focusout"].forEach(function (ev) {
      carousel.addEventListener(ev, function () { held = false; syncTimer(); });
    });
    document.addEventListener("visibilitychange", syncTimer);
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
    setPaused(false);
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

  /* ---------- 滚动联动：进度条、导航吸起、回到顶部 ---------- */
  /* 三件事共用一个 rAF 循环：scroll 一次手势能触发上百次，读布局/写样式每帧最多一次，
     否则手机上这几处合成会互相抢主线程，滚起来就是掉帧。 */
  var progress = document.querySelector(".progress");
  var topbar = document.querySelector(".topbar");
  var topBtn = document.getElementById("topBtn");
  if (progress || topbar || topBtn) {
    var frame = 0;
    var paint = function () {
      frame = 0;
      var box = document.documentElement;
      var y = window.pageYOffset || box.scrollTop || 0;
      var rest = box.scrollHeight - window.innerHeight;
      var ratio = rest > 0 ? Math.min(1, Math.max(0, y / rest)) : 0;
      if (progress) progress.style.transform = "scaleX(" + ratio.toFixed(4) + ")";
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

  /* ---------- 卡片追光 ---------- */
  /* 把命中点换算成卡片自己的百分比坐标写进 --mx/--my，CSS 用这两个值定位那团
     radial-gradient——光跟着指针走，卡片就像块会被反光的玻璃。
     只在真指针设备绑：手机上没有「悬停」，绑了会在最后一次触摸的位置留一坨光。
     JS 没跑或条件不匹配时，CSS 的默认值让它仍是中间那团正常的 hover 光。 */
  var finePointer = window.matchMedia &&
    window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  if (finePointer) {
    [].slice.call(document.querySelectorAll(".cards")).forEach(function (list) {
      function cardOf(node) {
        while (node && node !== list) {           /* 不用 closest()：老 WebView 上没有 */
          if (node.tagName === "LI") return node;
          node = node.parentNode;
        }
        return null;
      }
      var cframe = 0, hit = null;
      function apply() {
        cframe = 0;
        var p = hit; hit = null;
        if (!p || !p.card || !p.card.style) return;
        var r = p.card.getBoundingClientRect();
        if (!r.width || !r.height) return;
        p.card.style.setProperty("--mx", ((p.x - r.left) / r.width * 100).toFixed(1) + "%");
        p.card.style.setProperty("--my", ((p.y - r.top) / r.height * 100).toFixed(1) + "%");
      }
      list.addEventListener("pointermove", function (e) {
        hit = { card: cardOf(e.target), x: e.clientX, y: e.clientY };
        if (!cframe) cframe = raf(apply);
      }, { passive: true });
    });
  }
})();
