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

  function raw(name, arg) {
    try { return JSON.parse(B[name](arg === undefined ? "" : arg)); }
    catch (e) { return null; }        // 桥没应答、老壳缺这个方法、JSON 坏了，一律当"没有"
  }

  /* 有没有桥由 capabilities() 说了算：assistantShell 这个对象可能因为页面不在
     我们的主机上而调不通（壳那边按 origin 拒绝），那时 present 必须为 false，
     否则界面会摆出一套点了没反应的控件。 */
  const caps = (typeof B === "object" && B !== null) ? raw("capabilities") : null;
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
