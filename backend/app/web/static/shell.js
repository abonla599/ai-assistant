/* 原生壳（android/ 里那个 WebView）的适配层。
 *
 * 浏览器里没有 window.AssistantShell：这时 present 是 false，下面每个方法都退化成
 * "什么都不做 / 返回空"，页面其余功能一切照旧。这条降级不是兼容性装饰——同一份 JS
 * 既跑在 APK 里也跑在直接访问网址的人手里，而本项目的前端验收就是在没有壳的
 * headless 浏览器里跑的（spec §2 末尾、§6 那张表的最后一行）。
 *
 * 桥的八个方法一字不差、全部同步返回一段 JSON 字符串：
 *   capabilities() setOwner(user) scheduleReminder(json) cancelReminder(id)
 *   listReminders() pendingShares() readShareChunk(json) consumeShare(id)
 * 方法只加不减不改语义（老壳还在人手上，改名等于静默少一项功能）。
 *
 * 原生 → JS 只有一个入口 window.__shellEvent(payload)，payload 是**只含 type 与 id**
 * 的 JSON 字符串。内容（分享来的文件名、提醒正文）一律由 JS 拿 id 回查：外部可控
 * 字符串如果被拼进 evaluateJavascript 就是一段 JS 注入（spec §2 铁律①）。
 */

/* 监听器数组放在 IIFE 外面：全局回调 __shellEvent 与 SHELL.onEvent 必须用同一份，
   放进去就等于把 __shellEvent 绑死在这个文件的作用域里。 */
const SHELL_LISTENERS = [];

const SHELL = (() => {
  const B = window.AssistantShell;
  const CHUNK = 512 * 1024;      // 与壳的 readShareChunk 单次上限同一个数（spec §2 铁律③）
  const ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789";

  /* 调用约定：**Java 侧不收参的方法，一个参数都不许传**。
     `B[name]("")` 与 `B[name]()` 在这座桥上不是同一件事——桥是按"方法名 + 参数表"去
     找 Java 方法的，多传一个空串有可能根本匹配不到；那样 JS 拿到 undefined，
     下面那个 JSON.parse 抛错，被 catch 咽成 null，于是 present=false：
     **整个壳在页面上凭空消失，而界面长得跟浏览器一模一样，一个字都不报错。**
     零参的那四个：capabilities / checkUpdate / listReminders / pendingShares
     （对照 ShellBridge.java 的签名）。2026-09-22 真机第一次装 v0.17 就是这个表现，
     判据是 test_web_pwa.py 里那条"在 node 里真跑 shell.js、数每个方法收到几个参数"的锁——
     JVM 那 42 条单测是直接调 Java 方法的，永远碰不到这条派发规则。 */
  function raw(name, arg) {
    try { return JSON.parse(arg === undefined ? B[name]() : B[name](arg)); }
    catch (e) { return null; }        // 桥没应答、老壳缺这个方法、JSON 坏了，一律当"没有"
  }

  /* 桥诊断：只记"看到了什么"，不解释、不猜原因。present=false 之后界面与浏览器完全同形，
     而这正是这仓最难查的那类静默失效，所以无桥时把这三样显示到「设置 → 关于」：
     AssistantShell 这个对象在不在、capabilities() 到底回了什么、页面自认为在哪个源上。 */
  const DIAG = { object: typeof window.AssistantShell, reply: "没调用", text: "", page: "" };
  try { DIAG.page = location.protocol + "//" + location.host; } catch (e) {}

  /* 有没有桥由 capabilities() 说了算：assistantShell 这个对象可能因为页面不在
     我们的主机上而调不通（壳那边按 origin 拒绝，回的是字符串 "null"），那时 present
     必须为 false，否则界面会摆出一套点了没反应的控件。 */
  const caps = (() => {
    if (DIAG.object !== "object" || B === null) return null;
    let reply;
    try {
      reply = B.capabilities();
    } catch (e) {
      DIAG.reply = "调用抛错";
      DIAG.text = String((e && e.message) || e).slice(0, 60);
      return null;
    }
    // 先记下"桥回了什么类型"再解析：解析失败与"压根没匹配上方法（回 undefined）"是两种
    // 不同的坏法，混成一句"调用抛错"就把现场抹掉了——而那正是要靠这一行分清的东西。
    DIAG.reply = "回了 " + (reply === undefined ? "undefined" : typeof reply);
    DIAG.text = String(reply).slice(0, 60);
    try { return JSON.parse(reply); } catch (e) { return null; }
  })();
  const present = !!(caps && typeof caps === "object");

  function call(name, arg) {
    if (!present) return { ok: false, error: "no-shell" };
    const out = raw(name, arg);
    return (out && typeof out === "object") ? out : { ok: false, error: "bad-reply" };
  }

  function rows(name) {
    if (!present) return [];
    const out = raw(name);
    return Array.isArray(out) ? out : [];
  }

  /* 提醒的 id 由 JS 生成（r- + 12 位）：这样"这条提醒是谁建的"事实来源在网页，
     壳只是执行者。id 必须过壳那侧的 ^[A-Za-z0-9_-]{8,24}$，所以只用小写字母与数字。 */
  function newReminderId() {
    let tail = "";
    for (let i = 0; i < 12; i++) tail += ID_ALPHABET[Math.floor(Math.random() * ID_ALPHABET.length)];
    return "r-" + tail;
  }

  function decode(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }

  return {
    present,
    capabilities() { return present ? caps : {}; },
    /* 给「设置 → 关于」那行诊断用的原始记录。刻意不拼成一句人话、也不下结论：
       解释留给读它的人，界面上多一句猜测就是第二个事实来源。 */
    diagnostic() {
      return { object: DIAG.object, reply: DIAG.reply, text: DIAG.text, page: DIAG.page };
    },
    setOwner(user) {
      // 未登录 / 认不出人时传空串：壳那侧把 owner 清空，于是提醒与分享件全部不可见
      // （fail-closed，宁可看不见也不看见别人的）。
      if (present) call("setOwner", String(user || ""));
    },
    listReminders() { return rows("listReminders"); },
    /* 设置里那颗「检查更新」。只有 capabilities().update 在时才该被调用——
       老壳没这个方法，call() 会拿到 null 并回 {ok:false,error:"bad-reply"}，
       所以调用方必须先看过能力再决定这一行是走原生还是去下载页（app.js renderUpdateRow）。 */
    checkUpdate() { return call("checkUpdate"); },
    addReminder(reminder) {
      const r = reminder || {};
      return call("scheduleReminder", JSON.stringify({
        id: r.id || newReminderId(),
        at: Number(r.at) || 0,                 // epoch 毫秒：本地时区在 JS 这边算完再传
        title: String(r.title || ""),
        body: String(r.body || ""),
        repeat: r.repeat || "once",
      }));
    },
    cancelReminder(id) { return call("cancelReminder", String(id || "")); },
    pendingShares() { return rows("pendingShares"); },
    /* 分块取字节：一张 8MB 照片不该一次性穿过桥（上限 512KB/块，超了壳直接拒绝）。
       拿不到块（文件被系统清了或已过期）就返回 null，由调用方说"请重新分享一次"，
       而不是静默当成一个空附件。 */
    async readShare(id) {
      if (!present) return null;
      const meta = rows("pendingShares").find((s) => s && s.id === id);
      if (!meta) return null;
      const size = Number(meta.size) || 0;
      const parts = [];
      for (let offset = 0; offset < size; offset += CHUNK) {
        const chunk = call("readShareChunk", JSON.stringify({ id, offset, length: CHUNK }));
        if (!chunk.b64) return null;
        parts.push(decode(chunk.b64));
      }
      return new Blob(parts, { type: meta.mime || "application/octet-stream" });
    },
    consumeShare(id) { return call("consumeShare", String(id || "")); },
    onEvent(cb) { if (typeof cb === "function") SHELL_LISTENERS.push(cb); },
  };
})();

/* 壳推事件过来时唯一的落点。payload 是不可信输入：解析失败、没有 id 就整个丢掉，
   连"哪个事件"都不猜——内容永远由 JS 拿 id 反查（pendingShares / listReminders）。 */
window.__shellEvent = (payload) => {
  let evt;
  try { evt = JSON.parse(payload); } catch (e) { return; }
  if (!evt || typeof evt.id !== "string") return;
  SHELL_LISTENERS.forEach((cb) => { try { cb(evt); } catch (e) {} });
};
