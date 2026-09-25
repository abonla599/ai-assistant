/* AI 智能助手 PWA 主逻辑 */
"use strict";

const $ = (id) => document.getElementById(id);

/* ---------------- 本机身份清单（方案 C：只记"谁"，不记"凭什么"） ----------------
 * 一台机器上可能同时记着几个人（设置 → 账户）。清单条目只是显示与偏好用的人名册
 * （userId/username/role/lastSessionId/providerId），**不含任何凭据明文**：运行时的
 * 会话身份是服务端签发的 httpOnly Cookie，JS 读不到、也替不了它换人。
 * 直接后果两条，都是方案 C 明说的代价：
 * - 换到清单里的另一个人 = 重新登录一次（switchTo 只是把登录表单预填好）；
 * - 移除一个非当前的人 = 只忘掉这台机器上他的名字，服务端他那枚令牌无从代撤销。
 * pref.sessionId / pref.provider 都只是"当前那一条"的派生读取，写只能走
 * addIdentity / setCurrent / dropIdentity 三个入口。
 *
 * 老版本（Bearer 头时代）留在 localStorage 里的明文由 upgradeIdentitiesToCookie
 * 一次性洗掉：当前那位的那一枚会先被 adopt 成 Cookie（升级不掉线），其余的连同
 * 键名一起蒸发。
 */
const IDENTITY_CAP = 5;
const ID_KEY = "identities", CURRENT_KEY = "currentId";

function readIdentities() {
  let raw;
  try { raw = JSON.parse(localStorage.getItem(ID_KEY) || "[]"); }
  catch (e) { return []; }          // 手改坏的 JSON 不该把 app 锁死：当没记过人
  // 判据不再要 x.token：方案 C 的条目本来就不存凭据。老清单里残留的明文由
  // upgradeIdentitiesToCookie 在启动时统一洗掉。
  return Array.isArray(raw) ? raw.filter((x) => x && x.userId) : [];
}

function saveIdentities(list) { localStorage.setItem(ID_KEY, JSON.stringify(list)); }

/** 当前那一条。currentId 缺失、或指向一个已经不在清单里的人 → 取最新那条并写回。
 *  没有这条兜底就会出现「记着 5 个人但开 app 说没登录」，而那个症状有两种实现。 */
function currentEntry() {
  const list = readIdentities();
  if (!list.length) return null;
  const want = localStorage.getItem(CURRENT_KEY);
  let hit = list.find((x) => x.userId === want);
  if (!hit) {
    hit = list.slice().sort((a, b) => (b.addedAt || "").localeCompare(a.addedAt || ""))[0];
    localStorage.setItem(CURRENT_KEY, hit.userId);
  }
  return hit;
}

function patchCurrent(fields) {
  const hit = currentEntry();
  if (!hit) return;
  saveIdentities(readIdentities().map((x) =>
    x.userId === hit.userId ? Object.assign(x, fields) : x));
}

function setCurrent(userId) { localStorage.setItem(CURRENT_KEY, userId); }

function addIdentity(res) {
  // res 里可能有 token（登录/注册响应体），这里**一个字都不落**：清单只记"谁"。
  // adopt 已在 afterAuth 里把那枚明文换成了 httpOnly Cookie，明文到此为止。
  const list = readIdentities().filter((x) => x.userId !== res.user_id);
  list.push({ userId: res.user_id, username: res.username || "", role: res.role || "user",
              lastSessionId: "", providerId: "",
              addedAt: new Date().toISOString() });
  setCurrent(res.user_id);
  while (list.length > IDENTITY_CAP) {
    const oldest = list.slice().sort((a, b) =>
      (a.addedAt || "").localeCompare(b.addedAt || ""))[0];
    list.splice(list.indexOf(oldest), 1);
    // 顶号只忘本机：明文早就不在 JS 手里，替他撤销服务端会话这件事做不到了。
    // 兜底在服务端——每人令牌总数封顶（auth.MAX_SESSION_TOKENS）加管理员撤销。
  }
  saveIdentities(list);
}

/** 移除 = 忘掉这台机器上的人。
 *  当前这个人多一步真撤销：让服务端作废会话 Cookie 里那枚，再把 Cookie 本身刮掉。
 *  非当前的人只能忘本机——JS 早就不碰他的凭据明文，服务端那一枚只能靠
 *  总数封顶与管理员撤销收尾（方案 C 的既成代价，写在文件头那段里）。 */
async function dropIdentity(userId) {
  const hit = readIdentities().find((x) => x.userId === userId);
  if (!hit) return true;
  if ((currentEntry() || {}).userId === userId) {
    try {
      await API.logout();
    } catch (e) {
      /* 401/403 是"服务器本来就不认这枚会话"（账号被管理员删过、或被轮换过）：
         撤销要达到的目的已经达成，本机条目照删。其余失败（断网、5xx）留着条目——
         那种情况下会话可能还活着，"看起来删掉了但那枚还能用"比没删更糟。
         判据只看 HTTP 状态：文案会被服务端改，状态码不会。 */
      if (e.status !== 401 && e.status !== 403) {
        setStatus("没能退出那个账号：" + e.message + "；他还留在这台机器的清单里", true);
        return false;
      }
    }
  }
  saveIdentities(readIdentities().filter((x) => x.userId !== userId));
  return true;
}

/** loadWho 成功之后把服务端说的"我是谁"写回当前那条：清单里的 username/role
 *  只是显示用的，真身永远以 /v1/auth/me 为准。 */
function touchIdentity(me) {
  if (me) patchCurrent({ username: me.username, role: me.role });
}

/** 一次性的老键迁移：多身份之前这台机器只记着一个人。
 *  方案 C 之后它的职责多一条：accessToken 这个键现在是**要洗掉的明文**，
 *  无论清单迁没迁过，它都不许活过这一次启动。 */
function migrateLegacyIdentity() {
  const token = localStorage.getItem("accessToken");
  if (token && !localStorage.getItem(ID_KEY)) {
    const entry = { userId: localStorage.getItem("userId") || "manual",
                    username: "", role: "user",
                    lastSessionId: localStorage.getItem("sessionId") || "",
                    providerId: localStorage.getItem("provider") || "",
                    addedAt: new Date().toISOString() };
    saveIdentities([entry]);
    setCurrent(entry.userId);
  }
  ["accessToken", "userId", "sessionId", "provider"].forEach((k) => localStorage.removeItem(k));
}

/** 一次性升级到方案 C：把清单与老键里所有凭据明文洗出本机。
 *
 *  Bearer 头的年代里清单条目带 token 字段——那是躺在 localStorage 里谁都能读的
 *  会话凭据。升级路径只有一条正确的：当前那位的那枚先 adopt 成 httpOnly Cookie
 *  （人不掉线、也不逼他重打密码），其余的连同键名一起删——非当前那位的明文
 *  本来就不该再被任何请求头带上场，留着只有泄露价值。adopt 失败（口令已被撤销）
 *  同样删：拿一枚废令牌换一次"看起来在线"是自欺。
 *  跑完这条之后，全机任何存储里都不再存在凭据明文，这是本函数的唯一判据。
 */
async function upgradeIdentitiesToCookie() {
  migrateLegacyIdentity();
  let raw;
  try { raw = JSON.parse(localStorage.getItem(ID_KEY) || "[]"); }
  catch (e) { raw = []; }
  if (!Array.isArray(raw)) raw = [];
  const cur = currentEntry();
  let dirty = false;
  for (const x of raw) {
    if (x && typeof x.token === "string" && x.token) {
      if (cur && x.userId === cur.userId) {
        try { await API.adopt(x.token); } catch (e) { /* 废令牌：删得理直气壮 */ }
      }
      delete x.token;
      dirty = true;
    }
  }
  if (dirty) saveIdentities(raw.filter((x) => x && x.userId));
}

const pref = {
  get provider() { return (currentEntry() || {}).providerId || ""; },
  set provider(v) { patchCurrent({ providerId: v || "" }); },
  get sessionId() { return (currentEntry() || {}).lastSessionId || ""; },
  set sessionId(v) { patchCurrent({ lastSessionId: v || "" }); },
  get temperature() { return Number(localStorage.getItem("temperature") || 0.7); },
  set temperature(v) { localStorage.setItem("temperature", String(v)); },
  get contextTokensK() { return Number(localStorage.getItem("contextTokensK") || 8); },
  set contextTokensK(v) { localStorage.setItem("contextTokensK", String(v)); },
  get theme() {
    const saved = localStorage.getItem("theme");
    if (saved) return saved;
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  },
  set theme(v) { localStorage.setItem("theme", v); },
  /* 方案 C 起 pref 不再有 token 这一项——运行时凭据是 httpOnly Cookie，
   * JS 既读不到也不该想读。userId 仍是清单字段，只是"这台机器记着谁"。 */
  get userId() { return (currentEntry() || {}).userId || ""; },
  persona(sessionId) { return localStorage.getItem("persona:" + sessionId) || ""; },
  setPersona(sessionId, text) {
    text ? localStorage.setItem("persona:" + sessionId, text) : localStorage.removeItem("persona:" + sessionId);
  },
};

const state = {
  sessions: [],
  messages: [],
  providers: [],          // /v1/models 派生的清单
  serverDefault: null,    // /v1/models 报的"不给 id 时服务端会用哪个"；唯一答案
  presets: {},
  pending: [],            // 待发送附件 [{id,name,kind,size,url}]
  streaming: false,
  registering: false,     // 注册请求在途：与 streaming 同一个套路，挡双击
  switching: false,       // 切换在途：双击清单会并发跑两套"清屏 + 重取"，后到的盖住先到的
  controller: null,
  filter: "",
  memoryQuery: "",
  editingProvider: null,
  editingScope: null,     // "admin"=改共享条目（管理员面）| "mine"=我的模型 | null=表单没开
  myDefault: null,        // /v1/me/providers 报的"我的默认"，服务端持久化那份
  me: null,               // /v1/auth/me 的结果；null = 还不知道自己是谁
};

/* ---------------- 小工具 ---------------- */
function setStatus(text, isErr) {
  const el = $("statusHint");
  el.textContent = text || "";
  el.classList.toggle("err", !!isErr);
}

function applyTheme() {
  document.documentElement.dataset.theme = pref.theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = pref.theme === "light" ? "#ffffff" : "#0e1013";
  $("themeVal").textContent = pref.theme === "light" ? "浅色" : "深色";
}

function fmtSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

/* token 估算：中日韩一个字≈一个 token，其余约四个字符一个。
   是估算不是分词器——目的只有一个：让"上下文长度"用模型的真实刻度说话，
   而不是"一句很长的话"和"一个空洞"都算一条。 */
function estimateTokens(text) {
  const s = String(text || "");
  const cjk = (s.match(/[\u2e80-\u9fff\uf900-\ufaff\uff01-\uff60]/g) || []).length;
  return cjk + Math.ceil((s.length - cjk) / 4);
}

/* 按 token 预算从最近一条往回收：最后一条永远带上（它就是本轮要回答的话），
   往前遇到塞不下的就到此为止——不回头丢中间，那会把对话剪成读不懂的碎片。 */
function truncateWithin(list, budgetTokens) {
  const out = [];
  let used = 0;
  for (let i = list.length - 1; i >= 0; i--) {
    const cost = estimateTokens(list[i].content) + 4;   // +4：role/包装的固定开销
    if (out.length && used + cost > budgetTokens) break;
    out.unshift({ role: list[i].role, content: list[i].content });
    used += cost;
  }
  return out;
}

/* 老键存的是"条数"（2..40 条）。条和 token 不是一个刻度，就地按一条≈0.5k 换算
   写进新键；换算只发生一次，之后读数只走 contextTokensK。 */
function migrateContextPref() {
  if (localStorage.getItem("contextTokensK") !== null) return;
  const old = Number(localStorage.getItem("contextWindow") || 0);
  if (old > 0) {
    localStorage.setItem("contextTokensK",
      String(Math.min(60, Math.max(2, Math.round(old / 2)))));
  }
}

/* 当前模型允许把预算拖到多远（K token）：读模型档案的 max_context_k。
   后端 catalog/_public 已过一遍归一，这里拿到的必是数字；清单还没回来
   （冷启动、未登录）时兜到 64，与服务端缺省同一口径。 */
function modelCapK() {
  const p = currentProvider();
  return (p && p.max_context_k) || 64;
}

/* 设置里的「上下文长度」行：滑杆量程、步长与 `预算/上限` 显示随当前模型重画。
   清单在手才允许把超限的旧预算收敛写回——冷启动时上限还只是兜底值，
   那时就改写会把 256k 模型的设置误砍成 64。 */
function syncCtxRow() {
  const known = state.providers.length > 0;
  const cap = Math.max(2, modelCapK());
  const range = $("ctxRange");
  range.max = cap;
  range.step = cap <= 64 ? 2 : (cap <= 256 ? 8 : 32);
  if (known && pref.contextTokensK > cap) pref.contextTokensK = cap;
  range.value = pref.contextTokensK;
  $("ctxVal").textContent = pref.contextTokensK;
  $("ctxCap").textContent = cap;
}

function outbound() {
  const persona = pref.persona(pref.sessionId);
  const reserve = persona ? estimateTokens(persona) + 4 : 0;
  // 读数时再对模型上限取一次 min：syncCtxRow 的收敛可能还没跑过（刚切完模型、
  // 或这份 pref 是别的设备带来的大值），别指望界面刷新当唯一防线。
  const budgetK = Math.min(pref.contextTokensK, modelCapK());
  const msgs = truncateWithin(
    state.messages.filter((m) => m.content && !m.transient),
    Math.max(500, budgetK * 1000 - reserve));
  if (persona) msgs.unshift({ role: "system", content: persona });
  return msgs;
}

/** 401 有两种，糊成一句话会把人支使去填一个已经填对的框。
 *  - 本机压根没记过任何人：首启，该注册一个账号；
 *  - 记着人却被服务端拒：管理员撤销或轮换过会话，或这台机器换了人。
 * 两种都要把首屏凭据层挡在面前——它就在眼前，不必再去「设置」里找入口。
 * 403 不走这里：那是"身份是真的、角色不够"，换凭据没有用。
 *
 * 但"弹层已经开着"是第三种情况：那时人是坐在这层里敲字的，一条与表单不相干的后台
 * 401（同步消息、建会话、载入旧会话都会打到）不该把他敲到一半的密码整格抹掉——
 * showAuth 那一路经过 setAuthMode → clearAuthCredentials，见那里。所以这里只在
 * 弹层不可见时把它挡回来，可见时只更新状态条那一句话。
 */
function needsAuth(err) {
  if (!err || err.status !== 401) return false;
  // 判据从"本机有没有令牌"换成"本机记不记得人"：方案 C 下 JS 没有令牌可看。
  setStatus(currentEntry()
    ? "登录已失效：本机会话已被服务端拒绝（管理员撤销或轮换过），重新登录即可"
    : "还没有登录：用用户名和密码登录，或注册一个", true);
  if ($("authModal").classList.contains("hidden")) showAuth(currentEntry() ? "login" : "register");
  return true;
}

/* ---------------- 层栈：返回键与 Esc ----------------
 * 系统的返回键和键盘的 Esc 从这里走同一条路：退掉最上面那一层（实现见 layers.js）。
 * 每个界面自己负责"显示"，关闭只由栈在 popstate 里执行一次。所以规矩是：
 * 开一层调 openX()，收一层调 closeX()（它只是朝历史发一个请求），而 hideX() 是那段
 * 纯 DOM 的收尾、由栈来调。按钮里直接 classList.add("hidden") 就是让历史比屏幕上
 * 多出一层——症状是"返回要按两下才关一层"，正是本仓最恨的"效果没了但不报错"。
 */
const Layers = makeLayerStack(window.history);
window.addEventListener("popstate", (e) => Layers.reconcile(e.state));

/* ---------------- 首屏凭据层 ----------------
 * 登录与注册共用一张表单：注册只是多走一步——第一步定用户名与密码，第二步留三道
 * 找回题的答案。找回密码在同一层里换另一张表单（recoverForm），谁都不该是第二个弹窗。
 * 全程锁住按钮：手机双击会发出第二个 POST，注册那枪在第二下只会拿回"该用户名已存在"
 * （auth.py 里那句实话，界面把它落在用户名那一格下面），把已经成功的人显示成失败，
 * 还会两次一起抢会话落地（adopt→addIdentity）与渲染顺序。
 */
let authMode = "login";
let regStep = 1;

/* 找回的三道题全站固定，这一份就是前端唯一的那三句：界面自己渲染，不必问服务器要，
 * 于是"报出问题"那条能回答"这个用户名存在吗"的信道整个不存在了。列表顺序即答案
 * 顺序——记录里按这个次序存三枚摘要，第一格的答案不该拿去跟第三题比。
 *
 * 后端 app/core/auth.py 里有同一条常量。两边各改一版题面是**没有任何运行时报错**的
 * 坏法（人答的是另一套问题，找回永远只回一句"答案不正确"），所以由
 * tests/test_web_pwa.py 拿后端那份逐字比一次。别把这三句再抄到 index.html 里去。
 */
const RECOVERY_QUESTIONS = ["你的手机号后四位是什么？", "你小学在哪上？", "你父母姓氏的拼音首字母各一个是什么？"];

/** 题面从常量渲染进那六格（注册与找回各三道）。HTML 里没有第二份文字。 */
function renderRecoveryQuestions() {
  $("regQ1").textContent = RECOVERY_QUESTIONS[0];
  $("regQ2").textContent = RECOVERY_QUESTIONS[1];
  $("regQ3").textContent = RECOVERY_QUESTIONS[2];
  $("rcQ1").textContent = RECOVERY_QUESTIONS[0];
  $("rcQ2").textContent = RECOVERY_QUESTIONS[1];
  $("rcQ3").textContent = RECOVERY_QUESTIONS[2];
}

/** 三条答案按 RECOVERY_QUESTIONS 的次序交出去，后端按同一个次序比对三枚摘要。 */
function registerAnswers() {
  return [$("regAns1").value.trim(), $("regAns2").value.trim(), $("regAns3").value.trim()];
}

function recoveryAnswers() {
  return [$("rcAns1").value.trim(), $("rcAns2").value.trim(), $("rcAns3").value.trim()];
}

/** 离开一条流程 = 这一层的凭据格子一律清空。**进出这一层时清格子的地方只有这一处。**
 *  hidden 只是"看不见"，不是"没内容"：猜错拿 401 之后人还留在这一层，三句找回答案与
 *  新密码就躺在 DOM 里，点「回去登录」或被 needsAuth 重新弹层时一个字都没清。找回答案
 *  走的是和密码同一个慢哈希，它往往是个能猜的地名，所以同样是凭据。
 *  用户名不在名单里：留在屏内重试的人不该重敲名字，改密成功后还要把它回填给登录框。
 *  afterAuth 里那几行逐格清是另一回事（那里钉的是"清空必须晚于令牌落库"），别把两处并成
 *  一处：并了就把那条锁牵进来一起动了。
 */
function clearAuthCredentials() {
  $("authPass").value = "";
  $("authPass2").value = "";
  $("regAns1").value = "";
  $("regAns2").value = "";
  $("regAns3").value = "";
  $("rcAns1").value = "";
  $("rcAns2").value = "";
  $("rcAns3").value = "";
  $("rcNew").value = "";
  $("rcNew2").value = "";
}

function setAuthMode(mode) {
  authMode = mode === "register" ? "register" : "login";
  // 每次进这一层都从第一步开始：第二步那三格是上一次没提交出去的答案，
  // 留着它们再进来等于让人对着一屏填过的格子不知道从哪儿改。
  regStep = 1;
  // 先复位提示与 err 态，再清凭据：改密成功那句好消息是 showAuth→这里之后才写的，
  // 不复位就会沿用上一次的红色（.auth-hint.err）被读成失败。
  authFail("");
  setUserError("");
  clearAuthCredentials();
  const reg = authMode === "register";
  $("authSwitch").textContent = reg ? "已有账号？去登录" : "立即注册";
  // 密码管理器要分清"改密/新建"与"登录"，填错一半的话注册那枪会带上旧密码。
  $("authPass").autocomplete = reg ? "new-password" : "current-password";
  // "至少 8 位"这条规则只写在这里（placeholder）：后端改了下限而这里没改，
  // 用户就会在被拒之后对着一个看起来合规的框反复重试。
  $("authPass").placeholder = reg ? "请设置密码，至少 8 位" : "请输入密码";
  $("authSub").textContent = reg
    ? "注册分两步：先定用户名和密码，下一步留三道找回题的答案。"
    : "登录后继续；还没有账号就点下面的「立即注册」。";
  renderRegister();
}

/** 按模式与步骤决定哪些格子在场、主按钮叫什么——只有这一处在算这两件事。
 *  用户名那一格两步都留着：撞名（409）那句话就挂在它下面，藏起来那条信道就没落点了。
 */
function renderRegister() {
  const reg = authMode === "register";
  const step2 = reg && regStep === 2;
  $("authPassRow").classList.toggle("hidden", step2);
  $("authPass2Row").classList.toggle("hidden", !reg || step2);
  $("regStep2").classList.toggle("hidden", !step2);
  $("regBack").classList.toggle("hidden", !step2);
  // 还没有账号的人没有可找回的东西，这一步不该给他一条点了只会失败的链接
  $("authForgot").classList.toggle("hidden", reg);
  $("authGo").textContent = !reg ? "登录" : step2 ? "注册并登录" : "下一步";
}

/** 登录/注册那一块与找回那一块在同一层里互换：谁都不该是第二个弹窗。 */
function showAuthView(which) {
  const recovering = which === "recover";
  $("authForm").classList.toggle("hidden", recovering);
  $("recoverForm").classList.toggle("hidden", !recovering);
  if (recovering) {
    rcStep = 1;
    renderRecovery();
    rcFail("");
  }
}

/** 冷启动的中性第一态：整层盖住外壳，但只露品牌行和那句「正在确认身份…」。
 *  令牌是本机同步就读得到的，可"这枚令牌还有效吗"必须问服务端一趟；不问完就露出
 *  表单或外壳，用户看到的都是一次闪变。三条出路——认出人、要凭据、连不上——
 *  分别由 hideAuth / showAuth 收掉这一态，所以那两个函数里各清一次。
 */
function showAuthPending() {
  const modal = $("authModal");
  modal.classList.add("pending");
  modal.classList.remove("hidden");
  // 冷启动真正"第一次露出登录层"的是这里，不是 showAuth——showAuth 要等服务端答话。
  // 字标那一段在这里播；面板那一段等 clearAuthPending 摘掉中性态时才播。
  playAuthIntro();
}

function clearAuthPending() {
  const modal = $("authModal");
  modal.classList.remove("pending");
  // 中性态摘掉的那一刻才是"表单真的在屏上了"，面板那一段动画挂在这里而不是挂在
  // 弹层出现时——早一步播就是在空盒子上掀一下（pending 期间两张表单是 display:none）。
  playAuthSheet();
}

/* 开场动画的"只播一次"记在内存里：刷新一次就算一次新的冷启动，所以它该重来。
   落盘（pref.* 会进 localStorage）就变成"这台设备有生之年只看一次"，不是这个意思。 */
let introPlayed = false;
let introSheetPlayed = false;

/** 第一段：字标。冷启动第一帧上除了它什么都没有，所以它可以立刻播。 */
function playAuthIntro() {
  if (introPlayed) return;
  introPlayed = true;
  const modal = $("authModal");
  modal.classList.add("intro-brand");
  window.setTimeout(() => modal.classList.remove("intro-brand"), 500);
}

/** 第二段：弧形面板掀开。由 clearAuthPending 触发，一次页面生命周期只播一遍。 */
function playAuthSheet() {
  if (introSheetPlayed || !introPlayed) return;
  introSheetPlayed = true;
  const modal = $("authModal");
  modal.classList.add("intro-sheet");
  // 跑完摘类：CSS 里那些规则只以 .intro-sheet 为开关，摘掉之后切找回、掉线重弹
  // 都不会再放一遍；时长取动画本身（.5s）再加一点余量。
  window.setTimeout(() => modal.classList.remove("intro-sheet"), 700);
}

function showAuth(mode) {
  clearAuthPending();
  setAuthMode(mode || authMode);
  showAuthView("auth");
  $("authModal").classList.remove("hidden");
}

/** 眼睛只翻首屏这一格的 type：点一下是为了核对，不该顺手把人敲好的密码清掉。 */
function toggleAuthPass() {
  const pass = $("authPass");
  pass.type = pass.type === "text" ? "password" : "text";
  $("authEye").setAttribute("aria-label", pass.type === "text" ? "隐藏密码" : "显示密码");
}

function authFail(text) {
  const hint = $("authHint");
  hint.textContent = text || "";
  hint.classList.toggle("err", !!text);
}

function setUserError(text) {
  const el = $("authUserErr");
  el.textContent = text || "";
  el.classList.toggle("hidden", !text);
  if (text) $("authUser").focus();
}

/** 注册第一步的「下一步」：只做本地校验，一个字节都不发出去。
 *  此刻三道答案还空着，POST /v1/auth/register 必然 422，人看到的只是一句莫名的
 *  "注册失败"。确认密码本来就是纯前端语义（后端不该多一个 confirm_password 字段），
 *  不一致就该在这里停下，而不是带着不一致去挨一次服务器的手。
 */
function regNext() {
  authFail("");
  setUserError("");
  const username = $("authUser").value.trim();
  const password = $("authPass").value;
  if (!username) { authFail("用户名不能空着"); return; }
  if (!password) { authFail("密码不能空着"); return; }
  if (password !== $("authPass2").value) { authFail("两次输入的密码不一样"); return; }
  regStep = 2;
  renderRegister();
  $("regAns1").focus();
}

async function submitAuth() {
  if (state.registering) return;
  const reg = authMode === "register";
  // 步骤闸门贴在发请求这一侧再判一次：注册第一步的那一枪由 regNext() 接走，
  // 这里不留第二条能绕过它的路。
  if (reg && regStep === 1) { regNext(); return; }
  const username = $("authUser").value.trim();
  const password = $("authPass").value;
  authFail("");
  setUserError("");
  if (!username || !password) { authFail("用户名和密码都要填"); return; }
  let answers = null;
  if (reg) {
    answers = registerAnswers();
    // 少一格是手滑，本地先停下：后端那句"3 题答案都要填"没必要让人挨一次请求才看到
    if (answers.some((a) => !a)) { authFail("三道题的答案都要填，空一格就没法自救"); return; }
  }

  state.registering = true;
  $("authGo").disabled = true;
  $("authHint").textContent = reg ? "注册中…" : "登录中…";
  try {
    const res = reg
      ? await API.register(username, password, answers)
      : await API.login(username, password);
    await afterAuth(res);
  } catch (e) {
    if (e.status === 409) {
      // 重名是"改一下就好"的事，所以它写在用户名那一格下面，且不把表单末尾
      // 那句一起染红：两句话同屏时人会先去改密码。
      setUserError(e.message);
      authFail("");
    } else {
      // 后端已经把原因说成人话（密码太短 / 用户名或密码不正确 / 尝试次数过多），
      // 照实转述。
      authFail((reg ? "注册失败：" : "登录失败：") + e.message);
    }
  } finally {
    // 失败也得解锁：一次网络抖动不该把登录入口按死到刷新页面为止
    state.registering = false;
    $("authGo").disabled = false;
  }
}

/* ---------------- 密码找回（用户名 → 三道题 + 新密码，同一层里换字段） ----------
 * 第一步只收用户名，本地校验完就进第二步：服务器上已经没有"把问题念给你听"那个
 * 端点了，三题是常量、这里自己渲染。答案与新密码**一次**提交——分开验答案就等于
 * 给外人一个"这个答案对不对"的 oracle，那条免凭据信道上不该有这种东西。
 */
let rcStep = 1;
let rcName = "";

function renderRecovery() {
  const step2 = rcStep === 2;
  $("rcStep2").classList.toggle("hidden", !step2);
  // 第一步之后名字就定死了：改它得回去登录重来，不留"半路换人"的状态
  $("rcUser").disabled = step2;
  $("rcSub").textContent = step2
    ? "第 2 步：三道题的答案和新密码一起提交。"
    : "第 1 步：先输入你的用户名。";
  $("rcGo").textContent = step2 ? "改密码并去登录" : "下一步";
}

function rcFail(text) {
  const hint = $("rcHint");
  hint.textContent = text || "";
  hint.classList.toggle("err", !!text);
}

/** 找回第一步：只核对用户名填了没有，不发请求（没东西可问服务器）。 */
function rcNext() {
  rcFail("");
  rcName = $("rcUser").value.trim();
  if (!rcName) { rcFail("先填用户名"); return; }
  rcStep = 2;
  renderRecovery();
  $("rcAns1").focus();
}

async function submitRecovery() {
  if (state.registering) return;
  if (rcStep === 1) { rcNext(); return; }      // 第一步那一枪不发：题面是常量
  const answers = recoveryAnswers();
  const pw = $("rcNew").value;
  rcFail("");
  if (answers.some((a) => !a)) { rcFail("三道题的答案都要填"); return; }
  if (!pw) { rcFail("新密码不能空着"); return; }
  if (pw !== $("rcNew2").value) { rcFail("两次输入的新密码不一样"); return; }

  state.registering = true;
  $("rcGo").disabled = true;
  $("rcHint").textContent = "改密码中…";
  try {
    await API.resetPassword(rcName, answers, pw);
    // 改完密码就地续用（v0.21 双端同契约）：新密码刚被服务端验过，拿它直接登录，
    // 不再把人退回登录表单要求"再登一次"。别的设备掉线仍是这条路的**设计后果**，
    // 那句话不藏，只是不再挡在本人中间。
    rcStep = 1;
    renderRecovery();
    // 以前这一步是 showAuth→setAuthMode 顺路做的；续用不再经过 showAuth，就得在这儿
    // 亲手清——找回答案与新密码都是凭据，不许留在 DOM 里。用户名照旧留着。
    clearAuthCredentials();
    try {
      const res = await API.login(rcName, pw);
      await afterAuth(res);        // 收起弹层、落名册、拉数据，和正常登录一字不差
    } catch (e2) {
      // 自动登录没成（比如撞上限流）：退回老收口，名字预填好，只说一句人话。
      // 顺序是硬约束：showAuth 里的 setAuthMode 先把提示与 err 态复位，
      // 这句才不会被上一次失败的红色显示成错误。
      $("authUser").value = rcName;
      showAuth("login");
      $("authHint").textContent = "密码已重置，但自动登录没成：请用新密码再登一次。其他设备需要重新登录一次。";
    }
  } catch (e) {
    rcFail(e.message);
  } finally {
    state.registering = false;
    $("rcGo").disabled = false;
  }
}

function hideAuth() { clearAuthPending(); $("authModal").classList.add("hidden"); }

/** 拿到令牌之后的固定动作：明文当场收编成 httpOnly Cookie、落地人名册、重取身份
 *  与数据、收起这层。adopt 是全页面对 res.token 唯一的一次消费——它失败就等
 *  失败，让异常冒到调用处：没有 Cookie 的"登录成功"是假的，绝不能继续渲染。
 *  boot 那一次是在没有凭据的状态下跑的，模型清单与会话列表全是 401，不重跑就得
 *  叫用户手动刷新一次页面才算登录成功。 */
async function afterAuth(res) {
  await API.adopt(res.token);       // 凭据落地 = 换 Cookie；明文到此为止
  addIdentity(res);                 // 只记名字进来并把这个人设为当前身份，不记凭什么
  SHELL.setOwner(res.user_id);      // 告诉壳"现在是谁"：没这一步他看见的提醒是空集
  resetViewForIdentity();           // 从设置里添加第二个账户时，屏幕上正挂着第一个人的对话
  $("authPass").value = "";        // 密码不是运行时凭据，用完就清出输入框
  $("authPass2").value = "";
  // 找回答案走的是和密码同一个慢哈希，它往往是个能猜的地名——同样清出去
  $("regAns1").value = "";
  $("regAns2").value = "";
  $("regAns3").value = "";
  $("authHint").textContent = "";
  hideAuth();
  syncConnPane();
  $("registerHint").textContent = `已登录为 ${res.username}，正在载入…`;
  await loadWho();
  await loadServerData();
  renderMessages();
  $("registerHint").textContent = `已登录为 ${res.username}`;
}

/* ---------------- 身份与角色 ----------------
 * 角色只决定"看得见什么"，它从来不是边界：模型服务那 7 条路由在后端就是管理员
 * 专属，手搓请求照样 403。这里把入口收起来，是为了不让普通用户点进一个只会报
 * "需要管理员权限"的面板——那句话他自己解决不了，只会以为东西坏了。
 */
function isAdmin() {
  return (state.me || {}).role === "admin";
}

async function loadWho() {
  try {
    state.me = await API.me();
    setStatus("");      // 认出人了：上一轮"还没登录/连不上"那句已经过期
    touchIdentity(state.me);
    // 冷启动已登录、以及切换之后都走这一条：服务端说他是谁，壳那边就按谁隔离提醒
    // 与分享件。身份只多这一处出口，是因为清单里的 userId 只是本机记的，可能已作废。
    SHELL.setOwner(state.me.user_id);
  } catch (e) {
    state.me = null;
    // 401/403 是"这台设备还没登录"这一种正常状态；其余（连不上、服务端没配凭据
    // 的 503）得照原样抛出去，由 boot 说成后端连接问题。
    if (e.status !== 401 && e.status !== 403) throw e;
  }
  applyRole();
  return state.me;
}

function applyRole() {
  const admin = isAdmin();
  document.querySelectorAll("[data-admin-only]")
    .forEach((el) => el.classList.toggle("hidden", !admin));
  const name = (state.me || {}).username || "";
  $("userName").textContent = name || "未登录";
  /* 头像只取首字母：真实头像要么上传照片（多一处可写文件），要么引外部服务，
     都不值这一行的信息量 */
  $("userAvatar").textContent = name ? name[0] : "·";
  syncSetIdentity();
}

/* ---------------- 模型服务 ---------------- */
async function loadModels() {
  const data = await API.models();
  state.providers = data.models || [];
  // "默认是哪个"只有服务端知道答案（providers.ProviderStore.default() 已经只在
  // 可用的里面挑）。这里抄下来就用它，不再自己按 usable 顺序挑一遍——两侧各挑一次
  // 就是那条漂移：界面显示 B、实际调用用 A。
  state.serverDefault = data.default || null;
  state.presets = data.presets || {};

  const usable = state.providers.filter((p) => p.usable);
  if (!usable.length) {
    // 没有可用模型时两条路都通：管理员配全站共享，普通用户也能在同一个页面
    // 用自带 key 添加"我的模型"。所以不再把普通用户挡在页外，只给指路的一句话。
    if (isAdmin()) {
      setStatus("尚未配置可用的模型服务，请在「设置 → 模型服务」中添加", true);
      openSettings("providers");
    } else {
      setStatus("还没有可用模型：可在「设置 → 模型服务」用自己的 API Key 添加，或联系管理员配置", true);
    }
  } else {
    setStatus("");      // 有模型可用了：那句"没有服务"到此为止
  }
  /* "几个模型可用"这句现在有了正经落点：就写在选模型那一格下面，不再是侧栏
     底部那颗没人知道是什么意思的小圆点。 */
  $("chatNote").textContent = usable.length
    ? `${usable.length} 个模型可用` : "服务端还没有可用的模型";

  if (!usable.some((p) => p.id === pref.provider)) {
    const def = serverDefaultProvider();
    if (def) pref.provider = def.id;
  }
  renderModelSelect();
}

function renderModelSelect() {
  const sel = $("modelSel");
  sel.innerHTML = "";
  if (!state.providers.length) {
    const opt = document.createElement("option");
    opt.value = ""; opt.textContent = "未配置模型";
    sel.appendChild(opt);
    return;
  }
  state.providers.forEach((p) => {
    const opt = document.createElement("option");
    opt.value = p.id;
    if (p.usable) {
      opt.textContent = p.supports_vision ? `${p.name} · 支持图片` : p.name;
    } else {
      opt.textContent = `${p.name}（${p.reason || "不可用"}）`;
    }
    opt.disabled = !p.usable;
    sel.appendChild(opt);
  });
  sel.value = pref.provider;
  renderModelChip();
  syncCtxRow();   // 模型清单/当前模型变了，上下文行的量程与「/上限」跟着变
}

/* ---------------- 发送框旁的快速切换模型 ----------------
 * 芯片显示"当前会用哪个"（currentProvider 口径，与发送时真正用的一致），
 * 点开是纵向弹单：只列 usable 的，标出「共享/我的模型」，当前项打 ✓。
 * 选择即生效：本机 pref.provider 立刻换，服务端「我的默认」同步写一份
 * （存失败不反悔——localStorage 仍是这台设备的答案，与设置页下拉同一口径）。 */
function renderModelChip() {
  const usable = state.providers.filter((p) => p.usable);
  const chip = $("modelChip");
  if (!usable.length) {
    hideModelMenu();
    chip.classList.add("hidden");
    return;
  }
  chip.classList.remove("hidden");
  const cur = currentProvider();
  $("modelChipName").textContent = cur ? cur.name : "选择模型";
  const menu = $("modelMenu");
  menu.innerHTML = "";
  usable.forEach((p) => {
    const b = document.createElement("button");
    b.type = "button";
    b.setAttribute("role", "menuitem");
    const isCur = !!cur && p.id === cur.id;
    if (isCur) b.classList.add("current");
    const name = document.createElement("span");
    name.textContent = (isCur ? "✓ " : "") + p.name;
    const sub = document.createElement("span");
    sub.className = "mm-sub";
    sub.textContent = p.shared ? "共享" : "我的模型";
    b.append(name, sub);
    b.onclick = () => switchModel(p.id);
    menu.appendChild(b);
  });
}

function switchModel(id) {
  setModelMenu(false);
  if (id === pref.provider) return;
  pref.provider = id;
  API.setMyDefaultProvider(id).catch(() => {});
  setStatus("");
  renderModelSelect();   // 芯片与设置页下拉一起跟上，别留一份旧渲染
}

function setModelMenu(open) {
  if (!open) { Layers.close("modelMenu"); return; }
  $("modelMenu").classList.remove("hidden");
  $("modelChip").classList.add("open");
  Layers.open("modelMenu", hideModelMenu);
}

function hideModelMenu() {
  $("modelMenu").classList.add("hidden");
  $("modelChip").classList.remove("open");
}

function serverDefaultProvider() {
  // 服务端说用哪个就用哪个；只留一道"它在清单里、而且真可用"的核对：
  // default() 在有可用配置时不会给出不可用的那条，给出的那条不可用就等于
  // 一条都没有可用——那时返回 null，由调用方去说"没有可用模型"。
  const listed = state.providers.find((p) => p.id === state.serverDefault);
  return listed && listed.usable ? listed : null;
}

function currentProvider() {
  const listed = state.providers.find((p) => p.id === pref.provider);
  // 没选中、或选中的那个已经不可用（新身份的 providerId 是空的，管理员也可能刚把
  // 某家的密钥填坏）：退回服务端会用的那个默认，而不是把人拦在发送键上。
  return listed && listed.usable ? listed : serverDefaultProvider();
}

async function loadProviders() {
  const data = await API.myProviders();
  const admin = isAdmin();
  const shared = $("sharedProvList");
  shared.innerHTML = "";
  (data.shared || []).forEach((p) => shared.appendChild(providerRow(p, admin ? "admin" : "shared")));
  const mine = $("myProvList");
  mine.innerHTML = "";
  (data.mine || []).forEach((p) => mine.appendChild(providerRow(p, "mine")));
  state.myDefault = data.default || null;
  $("presetRow").innerHTML = "";
  Object.entries(data.presets || {}).forEach(([key, preset]) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = preset.label;
    b.onclick = () => fillFormFromPreset(preset);
    $("presetRow").appendChild(b);
  });
}

/* 一行的可操作性由 scope 决定，而不是由"看起来像谁的"决定：
   shared = 别人的/站级的共享条目，普通用户只读；
   admin  = 同一条共享条目，管理员拿 /v1/providers 那面全权管理；
   mine   = 这个人自己的私有条目，走 /v1/me/providers。
   ★「我的默认」人人可点（含共享条目）——它写的是这个人的偏好，不动别人的配置。 */
function providerRow(p, scope) {
  const el = document.createElement("div");
  el.className = "prov";

  const pm = document.createElement("div");
  pm.className = "pm";
  const b = document.createElement("b");
  let suffix = "";
  if (p.is_default && scope !== "mine") suffix += "（全站默认）";
  if (state.myDefault === p.id) suffix += "（我的默认）";
  b.textContent = p.label + suffix;
  const s = document.createElement("span");
  s.textContent = `${p.model} · ${p.base_url} · ${p.api_key_masked || "未填密钥"}`;
  pm.append(b, s);

  const tag = document.createElement("span");
  tag.className = "tag " + (p.has_key ? "ok" : "bad");
  tag.textContent = p.has_key ? (p.supports_vision ? "可用·视觉" : "可用") : "缺密钥";

  const ops = document.createElement("div");
  ops.className = "ops";
  const mk = (text, title, fn) => {
    const btn = document.createElement("button");
    btn.className = "icon-btn";
    btn.textContent = text;
    btn.title = title;
    btn.setAttribute("aria-label", title);
    btn.onclick = fn;
    return btn;
  };
  const reload = () => Promise.all([loadProviders(), loadModels()]);
  if (scope !== "shared") {
    ops.append(
      mk("✎", "编辑", () => openProviderForm(p, scope)),
      mk("⚡", "测试连通", async () => {
        const box = $("provTestResult");
        box.textContent = "测试中…";
        try {
          const r = scope === "admin" ? await API.testProvider(p.id) : await API.testMyProvider(p.id);
          box.textContent = (r.ok ? "✅ " : "❌ ") + r.detail;
        } catch (e) { box.textContent = "❌ " + e.message; }
      })
    );
  }
  if (p.has_key) {
    ops.append(mk("★", "设为我的默认", async () => {
      try { await API.setMyDefaultProvider(p.id); }
      catch (e) { /* 服务端存不住也要本机先生效：localStorage 是那台设备的答案 */ }
      pref.provider = p.id;
      await reload();
      renderModelSelect();
    }));
  }
  if (scope === "admin") {
    ops.append(mk("◎", "设为全站默认", async () => {
      await API.setDefaultProvider(p.id);
      await reload();
    }));
  }
  if (scope !== "shared") {
    ops.append(mk("×", "删除", async () => {
      if (!confirm(`删除「${p.label}」？`)) return;
      if (scope === "admin") await API.deleteProvider(p.id);
      else await API.deleteMyProvider(p.id);
      await reload();
    }));
  }

  el.append(pm, tag, ops);
  return el;
}

function openProviderForm(p, scope) {
  state.editingProvider = p.id;
  state.editingScope = scope;
  $("provId").value = p.id;
  $("provLabel").value = p.label;
  $("provBase").value = p.base_url;
  $("provModel").value = p.model;
  $("provCtxK").value = p.max_context_k || "";
  $("provVision").checked = !!p.supports_vision;
  $("provKey").value = "";
  $("provKey").placeholder = p.api_key_masked ? `已设置（${p.api_key_masked}），留空则不修改` : "填入 API Key";
  $("provTestResult").textContent = "";
  $("provForm").classList.remove("hidden");
}

function openBlankProviderForm(scope) {
  state.editingProvider = null;
  state.editingScope = scope;
  $("provId").value = ""; $("provLabel").value = ""; $("provBase").value = "";
  $("provKey").value = ""; $("provModel").value = ""; $("provCtxK").value = ""; $("provVision").checked = false;
  $("provKey").placeholder = "填入 API Key";
  $("provTestResult").textContent = "";
  $("provForm").classList.remove("hidden");
  $("provLabel").focus();
}

function fillFormFromPreset(preset) {
  if (!state.editingProvider) $("provLabel").value = preset.label;
  $("provBase").value = preset.base_url;
  $("provModel").value = preset.model;
  if (preset.max_context_k != null) $("provCtxK").value = preset.max_context_k;
  $("provVision").checked = !!preset.supports_vision;
}

function closeProviderForm() {
  state.editingProvider = null;
  state.editingScope = null;
  $("provForm").classList.add("hidden");
}

function providerDraftFromForm() {
  return {
    id: $("provId").value || undefined,
    label: $("provLabel").value.trim(),
    base_url: $("provBase").value.trim(),
    api_key: $("provKey").value.trim(),
    model: $("provModel").value.trim(),
    // 留空 → undefined → 服务端归一为缺省 64；判据（钳 1..10000）只写后端一处。
    max_context_k: Number($("provCtxK").value) || undefined,
    supports_vision: $("provVision").checked,
    is_default: false,
  };
}

/* 新条目默认落"我的模型"：表单从哪个按钮打开，保存就走哪一面。
   编辑共享条目只有管理员能进入表单（行上的 ✎ 只画给 admin），所以
   scope=admin 的编辑必然对得上 /v1/providers 的管理员要求。 */
function providerScopeIsMine() {
  return (state.editingScope || "mine") !== "admin";
}

async function saveProvider(ev) {
  ev.preventDefault();
  const draft = providerDraftFromForm();
  const box = $("provTestResult");
  const mine = providerScopeIsMine();
  try {
    if (state.editingProvider) {
      if (mine) await API.updateMyProvider(state.editingProvider, draft);
      else await API.updateProvider(state.editingProvider, draft);
    } else if (mine) {
      await API.addMyProvider(draft);
    } else {
      await API.addProvider(draft);
    }
    box.textContent = "✅ 已保存";
    closeProviderForm();
    await Promise.all([loadProviders(), loadModels()]);
  } catch (e) {
    box.textContent = "❌ " + e.message;
  }
}

async function testProviderDraft() {
  const box = $("provTestResult");
  box.textContent = "测试中…";
  try {
    const r = providerScopeIsMine()
      ? await API.testMyProviderDraft(providerDraftFromForm())
      : await API.testProviderDraft(providerDraftFromForm());
    box.textContent = (r.ok ? "✅ " : "❌ ") + r.detail;
  } catch (e) {
    box.textContent = "❌ " + e.message;
  }
}

/* ---------------- 附件 ---------------- */
function markChipFailed(chip, name, message) {
  chip.classList.add("failed");
  chip.innerHTML = "";
  const ico = document.createElement("span");
  ico.className = "att-ico";
  ico.textContent = "⚠";
  const msg = document.createElement("span");
  msg.className = "nm";
  msg.textContent = `${name}：${message}`;
  const rm = document.createElement("button");
  rm.type = "button";
  rm.textContent = "×";
  rm.setAttribute("aria-label", "移除失败的附件");
  rm.onclick = () => chip.remove();
  chip.append(ico, msg, rm);
}

async function pickFiles(input) {
  const files = [...input.files];
  input.value = "";
  for (const file of files) {
    const chip = attachmentChip({ name: file.name, size: file.size, kind: "?" }, true);
    $("attRow").appendChild(chip);
    try {
      const rec = await API.upload(file);
      chip.replaceWith(attachmentChip(rec));
      state.pending.push(rec);
    } catch (e) {
      markChipFailed(chip, file.name, e.message);
      setStatus("附件上传失败：" + e.message, true);
    }
  }
  updateSendEnabled();
}

function attachmentChip(rec, busy) {
  const el = document.createElement("div");
  el.className = "att-chip" + (busy ? " busy" : "");

  if (rec.kind === "image" && rec.url) {
    const img = document.createElement("img");
    img.src = rec.url; img.alt = "";
    el.appendChild(img);
  } else {
    const ico = document.createElement("span");
    ico.className = "att-ico";
    ico.textContent = rec.kind === "image" ? "🖼" : "📄";
    el.appendChild(ico);
  }

  const nm = document.createElement("span");
  nm.className = "nm";
  nm.textContent = rec.name;
  const sz = document.createElement("span");
  sz.className = "sz";
  sz.textContent = busy ? "上传中" : fmtSize(rec.size);
  el.append(nm, sz);

  if (!busy) {
    const rm = document.createElement("button");
    rm.type = "button";
    rm.textContent = "×";
    rm.setAttribute("aria-label", "移除附件");
    rm.onclick = () => {
      state.pending = state.pending.filter((a) => a.id !== rec.id);
      el.remove();
      updateSendEnabled();
    };
    el.appendChild(rm);
  }
  return el;
}

function clearPending() {
  state.pending.forEach((a) => a.url && URL.revokeObjectURL(a.url));
  state.pending = [];
  $("attRow").innerHTML = "";
}

function updateSendEnabled() {
  const text = $("input").value.trim();
  $("sendBtn").disabled = !state.streaming && !(text || state.pending.length);
}

/* 图片预览并发取（2026-09-22）。原先是 `for (const a of atts) { a.url = await ... }`：
 * 一张一张排队，历史里有 N 张图就是 N 趟串行往返，每趟走隧道实测 300~430ms，
 * 十条带图的历史光缩略图就要三秒多——这段等待和它锁住的 restore() 一起算在
 * 「打开网页到看见对话界面」那条链上。
 * 并发之后仍然保持的两条老语义，一条都不许松：
 * ① 每张图各自把结果写回**自己那条** a.url（不是先收齐再整批赋同一个值）；
 * ② catch 挂在每张图自己的链上、而不是整批上，所以坏一张只少一张缩略图，
 *    其余的照旧落地，整个函数也永远不会因为某一张 404 而 reject——
 *    「取不到就只显示文件名」说的是那一张，不是那一批。 */
async function hydrateImageUrls(atts) {
  const images = (atts || []).filter((a) => a.kind === "image" && !a.url);
  await Promise.all(images.map((a) => API.fileBlobUrl(a.id).then(
    (url) => { a.url = url; },
    () => { /* 取不到就只显示文件名 */ },
  )));
}

/* ---------------- 会话侧栏 ---------------- */
async function loadSessions() {
  const data = await API.listSessions();
  state.sessions = data.sessions || [];
  renderSessions();
}

function groupLabel(iso) {
  if (!iso) return "更早";
  const d = new Date(iso);
  if (isNaN(d)) return "更早";
  const today = new Date();
  const days = Math.floor((new Date(today.getFullYear(), today.getMonth(), today.getDate())
    - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
  if (days <= 0) return "今天";
  if (days === 1) return "昨天";
  if (days <= 7) return "近 7 天";
  if (days <= 30) return "近 30 天";
  return "更早";
}

function renderSessions() {
  const box = $("sessionGroups");
  box.innerHTML = "";
  const kw = state.filter.trim().toLowerCase();
  const list = state.sessions.filter((s) => !kw
    || (s.title || "").toLowerCase().includes(kw)
    || (s.session_id || "").toLowerCase().includes(kw));

  if (!list.length) {
    const p = document.createElement("div");
    p.className = "sb-group";
    p.textContent = kw ? "没有匹配的会话" : "还没有会话";
    box.appendChild(p);
    return;
  }

  let group = null;
  list.forEach((s) => {
    const label = groupLabel(s.created_at);
    if (label !== group) {
      group = label;
      const h = document.createElement("div");
      h.className = "sb-group";
      h.textContent = label;
      box.appendChild(h);
    }
    box.appendChild(sessionItem(s));
  });
}

function sessionItem(s) {
  const el = document.createElement("div");
  el.className = "sb-item" + (s.session_id === pref.sessionId ? " active" : "");

  const btn = document.createElement("button");
  btn.className = "title";
  btn.textContent = s.title || "新对话";
  btn.onclick = () => { switchSession(s.session_id); closeSidebar(); };

  const ops = document.createElement("div");
  ops.className = "ops";
  const del = document.createElement("button");
  del.className = "icon-btn";
  del.textContent = "×";
  del.setAttribute("aria-label", "删除会话");
  del.onclick = async (ev) => {
    ev.stopPropagation();
    if (!confirm("删除这个会话？")) return;
    await API.deleteSession(s.session_id);
    if (pref.sessionId === s.session_id) { pref.sessionId = ""; state.messages = []; renderMessages(); }
    await loadSessions();
    closeSidebar();
  };
  ops.appendChild(del);

  el.append(btn, ops);
  return el;
}

async function ensureSession() {
  if (pref.sessionId && state.sessions.some((s) => s.session_id === pref.sessionId)) return;
  const created = await API.createSession(pref.provider);
  pref.sessionId = created.session_id;
  state.messages = [];
  await loadSessions();
}

async function switchSession(id) {
  pref.sessionId = id;
  const full = await API.getSession(id);
  state.messages = (full.messages || []).map((m) => ({
    role: m.role, content: m.content, message_id: m.message_id, attachments: m.attachments,
  }));
  await hydrateImageUrls(state.messages.flatMap((m) => m.attachments || []));
  renderMessages();
  renderSessions();
  syncPersonaChip();
}

async function newChat() {
  pref.sessionId = "";
  state.messages = [];
  clearPending();
  await ensureSession();
  renderMessages();
  renderSessions();
  syncPersonaChip();
  $("input").focus();
}

/* ---------------- 渲染消息 ---------------- */
function syncTopTitle() {
  const s = state.sessions.find((x) => x.session_id === pref.sessionId);
  $("topTitle").textContent = (s && s.title) || "新对话";
}

function renderMessages() {
  syncTopTitle();          /* 换会话、改名、首条消息之后都从这里过一次，标题不会漏 */
  const host = $("messages");
  host.innerHTML = "";
  const inner = document.createElement("div");
  inner.className = "messages-inner";
  host.appendChild(inner);

  if (!state.messages.length) {
    inner.appendChild(emptyState());
    renderSuggestions(true);
    return;
  }
  renderSuggestions(false);
  state.messages.forEach((m, i) => inner.appendChild(messageNode(m, i)));
  host.scrollTop = host.scrollHeight;
}

function emptyState() {
  const el = document.createElement("div");
  el.className = "empty-state";
  const img = document.createElement("img");
  img.src = "icon.png"; img.alt = "";
  const h = document.createElement("h2");
  h.textContent = "开始一段对话";
  // 这里原先有一段"支持 Markdown…模型在设置里怎么挑"的说明：界面不教人怎么用，
  // 而且它指的那一页名已经变了。要读说明去 docs/用户手册.md。
  el.append(img, h);
  return el;
}

const SUGGESTIONS = [
  "帮我看看这段代码有什么问题",
  "用一句话解释什么是向量数据库",
  "我在准备网络安全考研，帮我规划复习",
  "把这段内容改得更简洁",
];

function renderSuggestions(show) {
  const box = $("suggestions");
  box.innerHTML = "";
  if (!show) return;
  SUGGESTIONS.forEach((text) => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = text;
    // 赋完 value 光标默认在句首，点标语想接着打字的人每次都得先跳回末尾——
    // focus 之后把光标钉到句尾（v0.21 原生端修过同一条，双端同契约）
    b.onclick = () => {
      const el = $("input");
      el.value = text; autosize(el); el.focus();
      el.setSelectionRange(text.length, text.length);
    };
    box.appendChild(b);
  });
}

function messageNode(m, index) {
  const node = $("msgTpl").content.firstElementChild.cloneNode(true);
  node.classList.add(m.role === "user" ? "user" : "assistant");

  const role = node.querySelector(".msg-role");
  role.textContent = m.role === "user" ? "我" : "助手";
  if (m.role !== "user" && m.model) role.textContent += " · " + m.model;

  const body = node.querySelector(".msg-body");
  if (m.role === "user") body.textContent = m.content;
  else MD.render(body, m.content);

  const atts = node.querySelector(".msg-atts");
  (m.attachments || []).forEach((a) => {
    if (a.kind === "image" && a.url) {
      const img = document.createElement("img");
      img.src = a.url; img.alt = a.name;
      atts.appendChild(img);
    } else {
      const tag = document.createElement("span");
      tag.className = "att-tag";
      tag.textContent = "📄 " + a.name;
      atts.appendChild(tag);
    }
  });
  if (!m.attachments) atts.remove();

  const tools = node.querySelector(".msg-tools");
  tools.querySelector('[data-act="regen"]').classList.toggle("hidden",
    m.role === "user" || index !== state.messages.length - 1);
  tools.querySelector('[data-act="good"]').classList.toggle("hidden", m.role !== "assistant");
  tools.querySelector('[data-act="bad"]').classList.toggle("hidden", m.role !== "assistant");
  if (m.feedback === 1) tools.querySelector('[data-act="good"]').classList.add("on");
  if (m.feedback === -1) tools.querySelector('[data-act="bad"]').classList.add("on");
  tools.querySelectorAll("button").forEach((btn) => {
    btn.onclick = () => onTool(btn.dataset.act, index, btn);
  });

  node.dataset.index = String(index);
  return node;
}

function scrollBottom() {
  const host = $("messages");
  host.scrollTop = host.scrollHeight;
}

/* ---------------- 消息操作 ---------------- */
async function onTool(act, index, btn) {
  const m = state.messages[index];
  if (!m) return;
  if (act === "copy") {
    try { await navigator.clipboard.writeText(m.content); btn.textContent = "已复制"; }
    catch (_) { btn.textContent = "失败"; }
    setTimeout(() => { btn.textContent = "复制"; }, 1500);
  } else if (act === "del") {
    await replaceMessages(state.messages.filter((_, i) => i !== index));
    state.messages.splice(index, 1);
    renderMessages();
  } else if (act === "regen") {
    await regenerate(index);
  } else if (act === "edit") {
    await editMessage(index, btn);
  } else if (act === "good" || act === "bad") {
    await sendFeedback(index, act === "good" ? 1 : -1, btn);
  }
}

async function editMessage(index, btn) {
  const node = $("messages").querySelector(`.msg[data-index="${index}"]`);
  if (!node) return;
  const body = node.querySelector(".msg-body");
  body.innerHTML = "";
  const box = document.createElement("textarea");
  box.className = "edit-box";
  box.rows = 3;
  box.value = state.messages[index].content;
  body.appendChild(box);
  box.focus();

  const commit = async (save) => {
    if (!save) { renderMessages(); return; }
    const text = box.value.trim();
    if (!text) { renderMessages(); return; }
    const next = state.messages.slice(0, index);
    next.push({ ...state.messages[index], content: text });
    await replaceMessages(next);
    state.messages = next;
    renderMessages();
    if (next[index].role === "user") await streamInto();
  };

  box.onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commit(true); }
    if (e.key === "Escape") commit(false);
  };
  btn.textContent = "保存";
  btn.onclick = () => commit(true);
}

async function regenerate(index) {
  const keep = state.messages.slice(0, index);
  await replaceMessages(keep);
  state.messages = keep;
  renderMessages();
  await streamInto();
}

async function streamInto() {
  const holder = { role: "assistant", content: "" };
  state.messages.push(holder);
  renderMessages();
  await runStream(holder);
}

async function replaceMessages(list) {
  if (!pref.sessionId) return;
  const payload = list
    .filter((m) => !m.transient)
    .map(({ role, content, message_id, memory_ids, model }) => {
      const out = { role, content };
      if (message_id) out.message_id = message_id;
      if (memory_ids) out.memory_ids = memory_ids;
      if (model) out.model = model;
      return out;
    });
  try {
    await API.replaceMessages(pref.sessionId, payload);
  } catch (e) {
    if (!needsAuth(e)) setStatus("同步到服务端失败：" + e.message, true);
  }
}

/* ---------------- 发送与流式 ---------------- */
async function send(text) {
  text = (text || "").trim();
  if ((!text && !state.pending.length) || state.streaming) return;

  if (!state.providers.length) {
    // 打开页面就按发送时模型清单还在路上：先拉一次。拉不到才承认"服务端没配"，
    // 否则一句"当前没有可用模型"会把一次正常的网络竞态说成服务坏了。
    try { await loadModels(); } catch (e) { /* 落到下面那句 */ }
  }

  if (!currentProvider()) {
    if (isAdmin()) {
      setStatus("当前没有可用模型，请在「设置 → 模型服务」中配置", true);
      openSettings("providers");
    } else {
      setStatus("当前没有可用模型：可在「设置 → 模型服务」用自己的 API Key 添加模型，或联系管理员配置", true);
    }
    return;
  }

  try { await ensureSession(); }
  catch (e) { if (!needsAuth(e)) setStatus("会话创建失败：" + e.message, true); return; }

  const atts = state.pending.map((a) => ({ id: a.id, name: a.name, kind: a.kind, size: a.size, url: a.url }));
  state.messages.push({ role: "user", content: text, attachments: atts });
  clearPending();
  renderMessages();
  await streamInto();
}

async function runStream(holder) {
  state.streaming = true;
  state.controller = new AbortController();
  $("sendBtn").classList.add("hidden");
  $("stopBtn").classList.remove("hidden");
  setStatus("生成中…");

  const node = $("messages").querySelector(".messages-inner").lastElementChild;
  if (node) node.classList.add("typing");
  const body = node ? node.querySelector(".msg-body") : null;
  // 标记"这里正在流式渲染"：markdown.js 的 highlightPending() 补扫会跳过带
  // data-live 的容器——半截代码块此刻高亮是无效功，流结束后的全量渲染才补。
  // 摘除在 finally 里，renderMessages 整棵重建之前。
  if (body) body.setAttribute("data-live", "1");

  /* chunk 到达的频率远高于帧率：每个 chunk 都全量重渲 + 强制滚底，长回复是
     O(n²)，而且用户在生成期间一旦上翻，下一帧就被拽回底部。改成把「渲染 +
     滚底」合并进 rAF，每帧最多一次（rAF 不可用时退回定时器，兜底风格同
     site.js）；滚底只在本来贴底时做，上翻阅读不被打断。 */
  const host = $("messages");
  const raf = window.requestAnimationFrame || function (fn) { return setTimeout(fn, 32); };
  const cancelRaf = window.cancelAnimationFrame || clearTimeout;
  let pending = 0;
  const paint = () => {
    pending = 0;
    if (!body) return;
    // 贴底要先于渲染判断：渲染会撑高滚动区，渲染后再算就永远"不贴底"了。
    const stick = host.scrollHeight - host.scrollTop - host.clientHeight < 120;
    MD.render(body, holder.content, true);
    if (stick) host.scrollTop = host.scrollHeight;
  };

  const lastUser = state.messages[state.messages.length - 2] || {};
  const sent = outbound();
  const attachments = (lastUser.attachments || []).map((a) => a.id);
  const providerId = pref.provider;
  let failed = false;
  let aborted = false;

  try {
    const done = await API.streamChat(
      { model: providerId, provider: providerId, messages: sent, attachments,
        sessionId: pref.sessionId, signal: state.controller.signal },
      (chunk) => {
        holder.content += chunk;
        if (body && !pending) pending = raf(paint);
      });
    if (done && done.message_id) holder.message_id = done.message_id;
    if (done && done.model) holder.model = done.model;
  } catch (e) {
    if (e.name === "AbortError") {
      aborted = true;
      holder.content = (holder.content || "") + "\n\n_（已停止生成）_";
      setStatus("已停止生成");
    } else if (e.retryable === false) {
      failed = true;
      holder.transient = true;
      holder.content = holder.content ? holder.content + "\n\n⚠️ " + e.message : "⚠️ " + e.message;
      if (!needsAuth(e)) setStatus(e.message, true);
    } else {
      try {
        const data = await API.chat({ model: providerId, provider: providerId,
          messages: sent, attachments, session_id: pref.sessionId });
        holder.content = data.reply;
        holder.message_id = data.message_id;
        holder.model = data.model;
      } catch (e2) {
        failed = true;
        holder.transient = true;
        if (needsAuth(e2)) holder.content = "⚠️ " + e2.message;
        else { holder.content = "⚠️ " + e2.message; setStatus(e2.message, true); }
      }
    }
  } finally {
    // 未决的那帧要取消：流结束后 renderMessages 会整棵重建消息区，
    // 迟到的 paint 会打到已经摘掉的旧节点上。
    if (pending) cancelRaf(pending);
    if (node) node.classList.remove("typing");
    if (body) body.removeAttribute("data-live");
    state.streaming = false;
    state.controller = null;
    $("stopBtn").classList.add("hidden");
    $("sendBtn").classList.remove("hidden");
  }

  await persistCurrent();
  renderMessages();
  await loadSessions().catch(() => {});
  if (!failed && !aborted) setStatus("");
  updateSendEnabled();
  $("input").focus();
}

async function persistCurrent() {
  await replaceMessages(state.messages);
}

function stop() {
  if (state.controller) { try { state.controller.abort(); } catch (_) {} }
}

async function sendFeedback(index, rating, btn) {
  const m = state.messages[index];
  if (!m.message_id) { setStatus("这条回复缺少 message_id，无法提交反馈", true); return; }
  try {
    await API.feedback(m.message_id, rating);
    m.feedback = m.feedback === rating ? undefined : rating;
    renderMessages();
  } catch (e) { setStatus("反馈失败：" + e.message, true); }
}

/* ---------------- 设置弹层 ----------------
 * 一级是分组列表，二级页在同一个弹层内换 view（不新开一张弹层：手机上两层弹层
 * 意味着人不知道自己按哪个 × 才能出去）。二级页**不进弹层的 DOM 层级，但要进层栈**——
 * 返回键该先退回列表、再关弹层，而不是直接把整个设置关掉。
 */
const SET_PAGES = { providers: "模型服务", accounts: "账户",
                    persona: "角色设定", memory: "长期记忆", reminders: "提醒" };

function openSettings(page) {
  $("settings").classList.remove("hidden");
  Layers.open("settings", hideSettings);
  showSetList();
  if (page) openSetPage(page);
}
function hideSettings() { $("settings").classList.add("hidden"); }
function closeSettings() { Layers.close("settings"); }

function showSetList() {
  $("setPages").classList.add("hidden");
  $("setList").classList.remove("hidden");
}

function openSetPage(name) {
  const page = document.querySelector(`.set-page[data-page="${name}"]`);
  if (!page) { showSetList(); return; }
  $("setPageTitle").textContent = SET_PAGES[name];
  $("setList").classList.add("hidden");
  $("setPages").classList.remove("hidden");
  document.querySelectorAll(".set-page")
    .forEach((p) => p.classList.toggle("hidden", p !== page));
  Layers.open("setPage", showSetList);
  if (name === "providers") loadProviders();
  if (name === "memory") loadMemories();
  if (name === "persona") syncPersonaChip();
  if (name === "reminders") renderReminders($("paneReminders"));
  if (name === "accounts") { syncConnPane(); renderAccounts(); }
}
function closeSetPage() { Layers.close("setPage"); }

/** 账户页的两处回显。
 *  方案 C 起这里**不再回填任何令牌**——输入框只进不出：它的值只会被 adopt
 *  用掉一次，页面没有任何一处能把当前会话的凭据再读出来（读不出来才叫 httpOnly）。 */
function syncConnPane() {
  $("whoInfo").textContent = state.me
    ? `当前身份：${state.me.username}（${isAdmin() ? "管理员" : "普通用户"}）`
    : "未登录";
}

/** 账户页：这台机器上认识谁。当前那条打一个标记，其余每人一个「删除」。
 *  删除当前这个人会真撤销他的会话；删除别人只是把他从本机名册忘掉（方案 C 的
 *  既成代价，见文件头）。切换 = 预填他的名字去登录，不再是一键静默换人。 */
function renderAccounts() {
  const box = $("accountList");
  box.innerHTML = "";
  const here = (currentEntry() || {}).userId;
  readIdentities().slice()
    .sort((a, b) => (b.addedAt || "").localeCompare(a.addedAt || ""))
    .forEach((x) => {
      const row = document.createElement("div");
      row.className = "set-row" + (x.userId === here ? " set-current" : "");
      const name = document.createElement("span");
      name.className = "set-lbl";
      name.textContent = x.username || x.userId;
      const tag = document.createElement("span");
      tag.className = "set-val";
      tag.textContent = x.userId === here ? "当前" : "";
      row.append(name, tag);
      /* 当前这一行两颗按钮都不给：换人不需要按钮（已经是这个人），
         而"删除"落在自己身上只会把正在用的会话打断——误触的代价不对称。 */
      if (x.userId !== here) {
        const go = document.createElement("button");
        go.className = "set-mini";
        go.textContent = "切换账号";
        go.onclick = () => switchTo(x.userId);
        const del = document.createElement("button");
        del.className = "set-mini set-del";
        del.textContent = "删除账号";
        del.onclick = async () => {
          go.disabled = del.disabled = true;
          if (await dropIdentity(x.userId)) renderAccounts();
          else go.disabled = del.disabled = false;
        };
        row.append(go, del);
      }
      box.append(row);
    });
  $("accountsVal").textContent = readIdentities().length + " 个已登录";
}

/** 设置 → 设备 → 检查更新。
 *
 *  同一行有两个元素（button 与 a），永远只露一个，差别在【这一行能不能真的办事】：
 *  1) 新壳（capabilities 带 update）→ 露 button，调桥，走的是与长按图标、桌面组件
 *     完全同一条路；
 *  2) 旧壳（v0.14/v0.15 的 capabilities 只有 shell）→ 认不到 update，露那个 a，
 *     href 直接指向下载页。少了这一档，这一行在装不到新壳的手机上就是个看起来能点的
 *     死按钮：桥里没有 checkUpdate 这个方法，点了连一句错都不会显示；
 *  3) 浏览器（没有壳）→ 两个都藏。那里没有"安装包"可更新，页面本身永远是从服务器
 *     现加载的那一版，摆一行只会让人以为网页能自更新。
 *
 *  下载页地址写在 index.html 那个 a 上，不在这里：
 *  test_frontend_uses_relative_api_paths_only 禁前端 JS 出现绝对 URL。
 *  点下去不改这一行的文字：原生那侧已经立刻弹了一条"正在检查更新…"的 Toast，
 *  这里再写一个"正在检查…"就会在对话框关掉之后一直挂着假状态。 */
// 服务端构建戳：/health 里的 build 字段，来自打包前生成的 version.txt。
// null = 还没取到；"" = 取到了但这台机器没有戳（老包或手工拷走的包）。
let serverBuild = null;

function fetchServerBuild() {
  if (serverBuild !== null) return;
  serverBuild = "";                       // 先占位，避免每次打开设置都发一次请求
  fetch("/health").then((res) => res.json()).then((j) => {
    const next = typeof j.build === "string" ? j.build : "";
    if (next !== serverBuild) { serverBuild = next; renderAboutRows(); }
  }).catch(() => {});                      // 取不到就维持"网页版"那句，不猜一个号
}

function renderAboutRows() {
  const btn = $("rowUpdate"), link = $("rowUpdateLink");
  if (!btn || !link) return;
  const caps = SHELL.present ? (SHELL.capabilities() || {}) : {};
  const shellVer = typeof caps.version === "string" && caps.version ? "v" + caps.version : "";
  const native = SHELL.present && !!caps.update;
  // 浏览器里也露"去下载页"那颗：这一行问的是"有没有新安装包"，跟有没有桥无关。
  // 只有新壳才调桥去查（它会比对已装的版本），其余一律给链接——包括旧壳和纯浏览器。
  const shown = native ? btn : link;

  [btn, link].forEach((el) => el.classList.toggle("hidden", el !== shown));
  // 这里不再动 set-only（那一类只干一件事：抹掉行底的分隔线）。以前这一行是
  // 「关于」卡片里的最后一行，另一颗只是 display:none、:last-child 看不见它，
  // 所以要手动补；现在它挪到了「版本」下面，上下都有行，分隔线本来就该在。

  // 版本这一行：壳在时报壳的 versionName（唯一来源 android/app/build.gradle），
  // 壳不在或旧壳不报时报**服务端**的构建戳（唯一来源是打包时的 git tag）。
  // 两个都不许写死在前端：抄一份进来，下次升版必然有一处是旧的，而且它显示得理直气壮。
  const box = $("versionVal");
  if (box) {
    const parts = [];
    if (shellVer) parts.push("壳 " + shellVer);
    if (serverBuild) parts.push("服务端 " + serverBuild);
    box.textContent = parts.length ? parts.join(" · ")
                                   : "网页版 · 界面随服务端更新，无需安装";
  }
  if (serverBuild === null) fetchServerBuild();

  /* 桥诊断：只在页面认不到壳的时候露一行。三个值全是从桥那侧来的（外部可控），
     所以只走 textContent，绝不拼进 innerHTML。
     后半句那个 typeof 判的是"两份文件不同批"：service worker 是按单个 URL 网络优先缓存的，
     一次刷新里 app.js 换新的而 shell.js 还是旧的，旧那份没有 diagnostic()——
     那就没有诊断可显示，而不是把整个「关于」页崩掉。 */
  const diag = $("bridgeDiag");
  if (diag) {
    if (SHELL.present || typeof SHELL.diagnostic !== "function") {
      diag.classList.add("hidden");
    } else {
      const d = SHELL.diagnostic();
      diag.textContent = "桥诊断：AssistantShell=" + d.object + " · capabilities() " + d.reply
                       + " 「" + d.text + "」 · 页面 " + d.page;
      diag.classList.remove("hidden");
    }
  }

  if (native) {
    $("updateVal").textContent = shellVer || serverBuild || "当前版本";
    $("updateLinkVal").textContent = "";
    btn.onclick = () => { SHELL.checkUpdate(); };
  } else {
    $("updateVal").textContent = "";
    $("updateLinkVal").textContent = (shellVer || serverBuild || "当前版本") + " · 去下载页";
  }
}

/** 「发现版本更新」底部卡片。
 *
 *  与上面那一行「检查更新」是两条独立的路，各自成立：那一行是**人去问**（点了才查），
 *  这一张是**替他看一眼然后提一句**。删掉任意一张，另一张照常工作。
 *
 *  为什么问的是服务端而不是页面自己 fetch GitHub：前端有一条硬锁不许出现绝对 URL，
 *  而大陆直连 GitHub 时好时坏——让一台机器扛比每台手机各自扛好，那一台机器上还有一致
 *  的 10 分钟缓存（判据在 core/releases.py）。这里因此只关心三件事：有没有新版、
 *  是哪一版、去哪儿下。
 *
 *  三条"不许吵到人"的规矩：
 *  ① 只在壳里出现。浏览器里没有安装包可换，弹一张"请更新"只会让人以为网页能自更新；
 *     而判断"是不是壳"用的是壳报上来的 versionName——老壳（v0.15 及更早）不报版本，
 *     于是它拿不到版本号，也就一句都不提。这不是遗漏：拿着手上的版本号才能回答
 *     "有没有更新"，猜一个号出来弹脸是更坏的选择。
 *  ② 一天最多一次。「稍后」与「立即更新」都写当天日期戳——他已经知道有新版了，
 *     同一天再弹第二次就成了骚扰。戳是"当天"而不是"这一次"：明天他还没更新，
 *     那就该再提一次。
 *  ③ 首屏不等它，失败静默。拉不到就是不提，绝不把"我读不到"演成"你已是最新"。 */
const UPDATE_SHEET_KEY = "updateSheetDay";

// 内存里那份探测结果。null = 这一页还没问过；问过了就复用，☰ 补弹时不再发请求。
let updateSeen = null;
let updateAsking = false;

/** 本机日历上的"今天"。不用 toISOString：那是 UTC，北京时间早上八点之前会写成昨天，
 *  于是"每天最多一次"在早上八点整被白嫖一次——同一天弹两次，恰好是这条节流要防的事。 */
function localDay() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}

function updateSheetShownToday() {
  try { return localStorage.getItem(UPDATE_SHEET_KEY) === localDay(); }
  catch (e) { return false; }      // 无痕模式里读就是抛：那就当今天没弹过，宁可多提一次
}

function markUpdateSheetShown() {
  try { localStorage.setItem(UPDATE_SHEET_KEY, localDay()); }
  catch (e) { /* 写不进去只是"今天可能多弹一次"，不值得为它打断更新提示本身 */ }
}

// 壳报的安装包版本（唯一来源 android/app/build.gradle）。浏览器与不报版本的老壳 → 空串。
function shellVersion() {
  if (!SHELL.present) return "";
  const caps = SHELL.capabilities() || {};
  return typeof caps.version === "string" ? caps.version.trim() : "";
}

function maybeAskUpdate() {
  const have = shellVersion();
  if (!have) return;
  if (updateSeen) { offerUpdate(updateSeen); return; }
  if (updateAsking) return;
  updateAsking = true;
  fetch("/v1/release/latest?have=" + encodeURIComponent(have))
    .then((res) => res.json())
    .then((j) => { updateSeen = j; offerUpdate(j); })
    // 连不上也把它当"问过了"存下来：不然每次开侧栏都替 GitHub 挡一次超时，
    // 而那场超时是用户在等界面回应，不是我们等的。
    .catch((e) => { updateSeen = { ok: false, reason: String(e && e.message || e) }; })
    .finally(() => { updateAsking = false; });
}

/** 只在"确实有更新"时弹。has_update 是三值的（true/false/null），null=不知道，
 *  不知道就不许弹——这是这张卡唯一的责任边界。 */
function offerUpdate(info) {
  if (!info || !info.ok || info.has_update !== true) return;
  if (updateSheetShownToday()) return;
  const sheet = $("updateSheet");
  const btn = $("updateGoBtn"), link = $("updateGoLink"), later = $("updateLaterBtn");
  if (!sheet || !btn || !link || !later) return;

  // 与设置那一行完全同一条分流：报得出 update 能力才给 button（调桥，走原生那条
  // 带进度与安装对话框的路），其余一律给 <a>。老壳只报版本号不报能力，这里就得给链接。
  const caps = SHELL.capabilities() || {};
  const native = SHELL.present && !!caps.update;
  const shown = native ? btn : link;
  [btn, link].forEach((el) => el.classList.toggle("hidden", el !== shown));
  later.classList.remove("hidden");

  const box = $("updateVer");
  if (box) {
    const parts = ["新版本 v" + info.latest];
    if (info.have) parts.push("当前 v" + info.have);
    if (info.size) parts.push(Math.round(info.size / 1024) + " KB");
    box.textContent = parts.join(" · ");
  }
  // 先摘掉 .show 再挂回去：同一页里第二次弹（明天那次）也要重放升起动画，
  // 而上一次留下的 .show 会让它变成"突然出现"。中间那次 reflow 是让浏览器真的
  // 把动画当成一次新开始，而不是接着上一轮跑完的状态。
  sheet.classList.remove("show");
  sheet.classList.remove("hidden");
  void sheet.offsetWidth;
  sheet.classList.add("show");
}

/* 纯 DOM 的收尾，**不进层栈**：这张卡是一次提示不是一个界面，所以"被别的层顶掉"不能
   等同于"他已经表过态了"。日期戳只在他自己按了两颗按钮之一（或链接跳走）时写——
   这就是它不登记历史的原因：一进栈，"收起"就多了一条没有用户意图的路，而那条路会顺手
   把"今天不再问"给记上。栈底的返回键因此直接退应用，与设置里那条规矩一致。 */
function hideUpdateSheet() {
  const sheet = $("updateSheet");
  if (sheet) { sheet.classList.add("hidden"); sheet.classList.remove("show"); }
  markUpdateSheetShown();
}

// ☰ 打开侧栏时补弹一次：只复用内存里那份，绝不重复发请求。
function reofferUpdate() {
  if (updateSeen) offerUpdate(updateSeen);
}

/** 一条提醒"到底响过没有"。firedAt 与 missed 是两回事，所以分着说：前者是"通知发出去了"
 *  （人看没看见网页不知道），后者是"到点了但没发出去"。
 *  老壳这两个键压根没有（undefined）→ 一个字都不说。那是"读不到"，不是"没响过"，
 *  把没查过的事说得像查过，比不答更糟——而这一屏的全部意义就是让这句话可信。 */
function fmtFiredHistory(r) {
  const fired = r ? r.firedAt : undefined, lost = r ? r.missed : undefined;
  if (typeof fired !== "number" || typeof lost !== "number") return "";
  const parts = [];
  if (fired) parts.push("上次发出 " + fmtReminderAt(fired));
  if (lost) parts.push(lost + " 次到点没发出");
  return parts.length ? parts.join(" · ") : "到点还没响过";
}

/** 提醒页顶部那一行常驻状态：通知给没给、闹钟排不排得出准点。
 *  三态（1 / 0 / 这个键压根没有）分开画，因为它们是三句不同的话——v0.17 及更早的壳
 *  不报这两个键，把它显示成"没授权"就是朝反方向说谎。
 *  值每次现问（见 shell.js capabilities()），从系统那一页回来时由 visibilitychange 重画。 */
function reminderStatusCard(caps) {
  const card = document.createElement("div");
  card.className = "set-card";
  card.dataset.role = "reminder-status";   // 只重画这一张时靠它认领，不靠"第一个 .set-card"猜
  const rows = [
    ["通知", caps.notifications, "notifications", "已授权", "没授权：到点发不出去"],
    ["闹钟", caps.exactAlarms, "alarms", "能准点", "没给精准闹钟：可能被省电推迟"],
  ];
  let known = 0;
  rows.forEach((pair) => {
    if (pair[1] !== 0 && pair[1] !== 1) return;
    known += 1;
    const row = document.createElement("div");
    row.className = "set-row";
    const lbl = document.createElement("span");
    lbl.className = "set-lbl";
    lbl.textContent = pair[0];
    const val = document.createElement("span");
    val.className = "set-val";
    val.textContent = pair[1] ? pair[3] : pair[4];
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "set-mini";
    btn.textContent = "去设置";
    // 只报"递没递出去"。那一页里改完回来靠 visibilitychange 重画，不在这里当场翻绿——
    // 点一下就说"已授权"是最容易骗到人的一种假状态。
    btn.onclick = () => {
      const r = SHELL.openSettings(pair[2]);
      if (!r.ok) setStatus("打不开系统那一页，请到系统设置里搜「AI 助手」", true);
    };
    row.append(lbl, val, btn);
    card.appendChild(row);
  });
  if (!known) {
    const note = document.createElement("p");
    note.className = "pane-note";
    note.textContent = "这版壳读不到通知与闹钟权限，升级壳后才能看到。";
    card.appendChild(note);
  }
  return card;
}

/** 提醒页（设置 → 设备 → 提醒）。
 *  没有桥时这一页只剩一句说明：提醒是壳在手机上排的，网页自己存一份就变成第二个
 *  事实来源，而且那一份永远不会响——按了没反应的表单比没有表单更坏。 */
function renderReminders(host) {
  host.innerHTML = "";
  if (!SHELL.present) {
    const note = document.createElement("p");
    note.className = "pane-note";
    note.textContent = "这里设的提醒只在这台手机的应用里生效。";
    host.appendChild(note);
    return;
  }

  const items = SHELL.listReminders();
  $("remindersVal").textContent = items.length ? items.length + " 条" : "";
  // 状态行排在表单之前、且空列表时也在：它回答的是"为什么不响"，一条提醒都没有的时候
  // 恰恰是最需要它的时刻。
  host.appendChild(reminderStatusCard(SHELL.capabilities()));

  const form = document.createElement("form");
  form.className = "row";
  const title = document.createElement("input");
  title.type = "text";
  title.placeholder = "提醒我什么";
  const at = document.createElement("input");
  at.type = "datetime-local";
  at.setAttribute("aria-label", "提醒时间");
  const repeat = document.createElement("select");
  repeat.setAttribute("aria-label", "重复");
  [["once", "只一次"], ["daily", "每天"], ["weekly", "每周"]].forEach((pair) => {
    const opt = document.createElement("option");
    opt.value = pair[0]; opt.textContent = pair[1];
    repeat.appendChild(opt);
  });
  const add = document.createElement("button");
  add.className = "btn btn-primary";
  add.textContent = "添加";
  form.append(title, at, repeat, add);
  form.onsubmit = (e) => {
    e.preventDefault();
    const text = title.value.trim();
    const when = at.value ? new Date(at.value).getTime() : NaN;
    if (!text) { setStatus("先写上要提醒什么", true); return; }
    if (isNaN(when)) { setStatus("时间还没选好", true); return; }
    // at 交出去的是 epoch 毫秒：本地时区在网页这边算完，壳那边不做任何日历运算。
    const r = SHELL.addReminder({ at: when, title: text, body: "", repeat: repeat.value });
    if (!r.ok) {
      setStatus(r.error === "too_many" ? "这个人已经有 32 条提醒了，先取消几条"
                                       : "没能设这条提醒：" + (r.error || "壳没有应答"), true);
      return;
    }
    setStatus("");
    renderReminders(host);
  };
  host.appendChild(form);

  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "pane-note";
    empty.textContent = "还没有提醒";
    host.appendChild(empty);
    return;
  }

  const card = document.createElement("div");
  card.className = "set-card";
  items.forEach((r) => {
    const row = document.createElement("div");
    row.className = "set-row";
    const lbl = document.createElement("span");
    lbl.className = "set-lbl";
    lbl.textContent = r.title || "提醒";
    const val = document.createElement("span");
    val.className = "set-val";
    const hist = fmtFiredHistory(r);
    val.textContent = fmtReminderAt(r.at)
      + (r.repeat === "daily" ? " 每天" : r.repeat === "weekly" ? " 每周" : "")
      + (hist ? " · " + hist : "");
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "set-mini set-del";
    cancel.textContent = "取消";
    cancel.onclick = () => { SHELL.cancelReminder(r.id); renderReminders(host); };
    row.append(lbl, val, cancel);
    card.appendChild(row);
  });
  host.appendChild(card);
}

/* 从系统那一页（点「去设置」过去的）回到应用时，WebView 不会重新加载页面，那一行还挂着
   旧权限。这里只换状态这一张卡片、不整页重画：整页重画会连带清掉他刚打进表单却没点
   "添加"的那句提醒——为了刷新两个权限字丢掉一条正在写的提醒，是拿一个真问题换一个假问题。
   列表里的"上次发出/几次没发出"不在这里跟：它只在提醒真的到点时变，而那时壳会推一条
   reminder 事件过来，onShellEvent 会整页重画。 */
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible" || !SHELL.present) return;
  const page = $("paneReminders");
  const old = page && page.querySelector('[data-role="reminder-status"]');
  if (page && old && !page.classList.contains("hidden")) {
    page.replaceChild(reminderStatusCard(SHELL.capabilities()), old);
  }
});

/** 提醒行上的时间：月/日 时:分。跨年的提醒在本产品里没有意义，不值得占这一行。 */
function fmtReminderAt(ms) {
  const d = new Date(Number(ms));
  if (isNaN(d)) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/* ---------------- 壳的系统分享入口 ----------------
 * 相册/文件里点"分享 → AI 助手"，件先躺在壳的待上传队列里；这里把它读出来、
 * 走**现有**那条 POST /v1/uploads（api.js 的 upload：令牌头、错误解析、附件记录
 * 形状都只对齐一次），拿回附件记录挂进待发送区，然后让壳删掉本地那份。
 */
async function drainShares() {
  if (!SHELL.present) return;                 // 浏览器里没有队列：第一句就出去
  const items = SHELL.pendingShares();
  for (const meta of items) {
    const chip = attachmentChip({ name: meta.name, size: Number(meta.size) || 0, kind: "?" }, true);
    $("attRow").appendChild(chip);
    try {
      const blob = await SHELL.readShare(meta.id);
      // cacheDir 里的东西系统随时可能自己清掉；读不到要人说"重新分享一次"，不能静默
      if (!blob) throw new Error("已经不在这台手机上了，请重新分享一次");
      const file = new File([blob], meta.name || "shared", { type: blob.type });
      const rec = await API.upload(file);
      chip.replaceWith(attachmentChip(rec));
      state.pending.push(rec);
      SHELL.consumeShare(meta.id);
    } catch (e) {
      markChipFailed(chip, meta.name, e.message);
      setStatus("分享的文件上传失败：" + e.message, true);
    }
  }
  updateSendEnabled();
}

/** 壳拒收分享的五个固定码 → 一句话。键必须与壳的 SharePolicy.Intake 小写名一一对应
 *  （钉在 backend/tests/test_web_pwa.py）；落在之外的码走 onShellEvent 的兜底句。 */
const SHARE_REFUSAL = {
  too_large: "分享没收下：文件太大，超出了助手的附件上限",
  text_too_large: "分享没收下：这段文本太长",
  unsupported_mime: "分享没收下：这种内容助手不收",
  no_stream: "分享没收下：里面没有内容",
  read_failed: "分享没收下：没读到内容，请重新分享一次",
};

/** 壳推来的事件只带 {type,id}：所以这里只决定"该重取什么"，不决定"内容是什么"。 */
function onShellEvent(evt) {
  if (evt.type === "share") { drainShares(); return; }
  // 点了到点的通知：把这一页刷成最新排期（没开着也无所谓，render 是幂等的）
  if (evt.type === "reminder") renderReminders($("paneReminders"));
  /* 桌面组件那两个按钮与长按图标的两条快捷方式（壳的 Task 8）。id 只会是 camera /
     new_chat 这两个固定值——壳那侧 ShellEvents.fromOpenFrom 有白名单，第三种值发不过来；
     而"不直接拉相机"是零依赖逼出来的取舍（FileProvider 在 androidx.core 里）。
     这里刻意不加"先检查登录态"那一层：openCamera 与 newChat 各自会撞上已有的鉴权路径
     （getUserMedia 要相机权限、ensureSession 要令牌），多一层判断就是多一套规则。 */
  if (evt.type === "open") {
    if (evt.id === "camera") openCamera();
    else if (evt.id === "new_chat") newChat();
  }
  /* 分享被壳拒收（壳的 Task 3 加固）：id 是 SharePolicy 那五个固定码之一。
     为什么网页也要说一遍：ShareActivity 是 Theme.NoDisplay，全程没有窗口，而官方
     Toast 文档写明文字 Toast 只在应用处于前台时显示——那条即时反馈在这台手机上
     随时可能被系统掐掉。两条路互不依赖，缺一条还剩一条。
     文案里不写体积数字：上限住在壳的 ShareInbox（10MB / 文本 1MB），这里抄一份
     就成了第二个真相，壳改了数字网页就开始说谎。 */
  if (evt.type === "share_rejected") {
    setStatus(SHARE_REFUSAL[evt.id] || "分享没收下，请重新分享一次", true);
  }
}

/** 切换前必须一次清掉的本机视图。
 *  漏一项就是"界面写着 B、屏幕上画着 A 的对话"——用户据此判断「账号之间记忆共享」，
 *  哪怕服务端从来没共享过。所以这 7 项列在一处，而不是散在切换路径里各清各的。 */
function resetViewForIdentity() {
  state.messages = [];
  state.sessions = [];
  state.pending = [];
  state.memoryQuery = "";
  state.filter = "";
  $("input").value = "";
  $("memoryList").innerHTML = "";
  $("personaInput").value = "";
  $("memoryQuery").value = "";
  // 刻意不动 lastSessionId：setCurrent 之后 pref.sessionId 读到的就是新那个人自己
  // 记着的那条。把上一个人的指针一起擦掉，切回来就变成"我刚才聊的呢？"（实测）。
  renderMessages();
  renderSessions();
}

/** 换到清单里的另一个人 = 以他的身份再登录一次。
 *  方案 C 下这是唯一诚实的做法：会话 Cookie 只装得下一个人，JS 手里也没有他的
 *  凭据可以静默换——换指针不换会话，就会出现"界面写着 B、Cookie 带着 A"，
 *  那正是本项目为跨用户泄露付过一次账的形状。所以这里只把登录表单预填好，
 *  currentId 等 afterAuth 真登录成功再落。 */
async function switchTo(userId) {
  if (state.switching) return;
  const hit = readIdentities().find((x) => x.userId === userId);
  if (!hit) return;
  state.switching = true;
  try {
    if (state.controller) state.controller.abort();
    state.controller = null;
    state.streaming = false;
    closeSettings();
    showAuth("login");
    $("authUser").value = hit.username || "";
    setStatus("换到 " + (hit.username || hit.userId) + " 需要再输一次他的密码（会话凭据不在本机明文里）");
  } finally {
    state.switching = false;
  }
}

/** 退出这台机器：作废当前这一枚会话，并把这个人从清单里去掉。
 *  只删本机不撤销就是个假动作——Cookie 里那枚在服务端还活着。
 *  退完之后清单里剩下的人也没有一个是"在线"的：会话只装过刚退掉那位。
 *  所以不自动指向别人——currentId 决定偏好写进谁的清单，指到一个没登录的人
 *  身上就是埋雷；只把登录面预填成最新那位的名字，由人自己决定登谁。 */
async function logoutCurrent() {
  const hit = currentEntry();
  if (!hit) { showAuth("login"); return; }
  if (!await dropIdentity(hit.userId)) return;
  resetViewForIdentity();
  // 挑"最新一条"与 currentEntry 的兜底同一条规则：清单的插入顺序里可能躺着已被
  // 删除的账号，不排序会预填一个根本不存在的人的名字。
  const rest = readIdentities().slice()
    .sort((a, b) => (b.addedAt || "").localeCompare(a.addedAt || ""));
  if (rest.length) { showAuth("login"); $("authUser").value = rest[0].username || ""; }
  else showAuth("register");
}

function emptyItem(text) {
  const li = document.createElement("li");
  li.className = "empty";
  li.textContent = text;
  return li;
}

async function loadMemories() {
  const ul = $("memoryList");
  ul.innerHTML = "";
  try {
    // 20 = 后端肯给的条数上限（api.js 的 MEMORY_TOP_K_MAX，两处由测试对齐）。
    // 以前这里发 30，后端 le=20 直接 422：搜索框永远是坏的，而看起来只是"没结果"。
    const data = state.memoryQuery
      ? await API.searchMemory(state.memoryQuery, 20)
      : await API.listMemory(50);
    const items = data.memories || data.results || [];
    // 行上的值只在"浏览全部"时写：搜索态那个数是筛选结果，不是库存量，写进去就是骗人。
    // listMemory 的上限是 50，正好 50 条说明后面还有，不能报成"50 条"。
    if (!state.memoryQuery) $("memoryVal").textContent = items.length >= 50 ? "50+ 条" : `${items.length} 条`;
    if (!items.length) {
      ul.appendChild(emptyItem(state.memoryQuery ? "没有匹配的记忆" : "还没有记忆"));
      return;
    }
    items.forEach((m) => {
      const li = document.createElement("li");
      const w = document.createElement("span");
      w.className = "w";
      w.textContent = Number(m.weight ?? (m.metadata || {}).weight ?? 1).toFixed(2);
      const txt = document.createElement("span");
      txt.className = "txt";
      txt.textContent = m.content || "";
      const del = document.createElement("button");
      del.textContent = "×";
      del.setAttribute("aria-label", "删除这条记忆");
      del.onclick = async () => {
        const id = m.id || m.memory_id;
        if (!id) { setStatus("这条记忆没有 id，无法删除", true); return; }
        await API.deleteMemory([id]).catch((e) => setStatus("删除失败：" + e.message, true));
        loadMemories();
      };
      li.append(w, txt, del);
      ul.appendChild(li);
    });
  } catch (e) {
    ul.appendChild(emptyItem(memoryListErrorText(e)));
  }
}

/** "记忆服务不可用"是一句会让人去做错事的话：重启后端治不了没登录。 */
function memoryListErrorText(e) {
  if (e.status === 401) return "未登录或令牌已失效：请在「设置 → 账户」重新注册";
  if (e.status === 403) return "这个账号没有读取记忆的权限，请找管理员确认";
  return "记忆服务不可用：" + e.message;
}

/** 设置一级列表顶上的身份卡。
 *  原先这里是一张 dl 回显（当前模型 / 登录身份 / 温度 · 上下文…）：模型在上一行
 *  就能改、温度根本不生效，回显等于把同一件事说两遍还捎带一个假数字。值现在
 *  只待在各自的行上，这一处只写"我是谁"。
 */
function syncSetIdentity() {
  const name = (state.me || {}).username || "";
  $("setAvatar").textContent = name ? name[0] : "·";
  $("setMe").textContent = name || "未登录";
  $("setRole").textContent = name ? (isAdmin() ? "管理员" : "普通用户") : "";
  $("accountsVal").textContent = readIdentities().length + " 个已登录";
  // 注册那句进度文字属于首层那一层弹层：层收起来之后它没有理由继续挂着，否则切回
  // 上一个人时，账户页底部还写着「已登录为 别人」。
  $("registerHint").textContent = "";
}

/* 角色只显示在设置那一页里：原先顶栏那颗 chip 是同一件事的第二个入口，
   而它写的"无角色"三个字并不告诉人点进去能干什么。 */
function syncPersonaChip() {
  $("personaInput").value = pref.persona(pref.sessionId);
  // 列表上就该看得见这一项有没有内容：点进去才发现"哦我写过"是多余的一步。
  $("personaVal").textContent = $("personaInput").value.trim() ? "已填写" : "";
}

/* 附件菜单是一颗气泡：开合都走栈，别处（拍照那一路）靠它排在栈顶时自动让位。 */
function setAttachMenu(open) {
  if (!open) { Layers.close("attachMenu"); return; }
  $("attachMenu").classList.remove("hidden");
  $("attachBtn").classList.add("open");
  Layers.open("attachMenu", hideAttachMenu);
}

function hideAttachMenu() {
  $("attachMenu").classList.add("hidden");
  $("attachBtn").classList.remove("open");
}

function toggleAttachMenu() {
  setAttachMenu($("attachMenu").classList.contains("hidden"));
}

/* ---------------- 网页内拍照 ----------------
 * 不用 <input capture>：部分安卓浏览器（WebView 外壳）会忽略 capture 与 accept，
 * 弹出自己的"相机/文件"面板，导致点相机还要再选一次。这里直接用 getUserMedia
 * 在页面内取景，完全不经过系统选择器。
 */
const cam = { stream: null, blob: null };

async function openCamera() {
  cam.blob = null;
  $("camHint").textContent = "";
  $("camCanvas").classList.add("hidden");
  $("camVideo").classList.remove("hidden");
  $("camShoot").classList.remove("hidden");
  $("camRetake").classList.add("hidden");
  $("camUse").classList.add("hidden");
  $("cameraModal").classList.remove("hidden");
  Layers.open("camera", hideCamera);

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    const reason = "该浏览器不支持网页相机，请使用文件选择器";
    $("camHint").textContent = reason;
    setStatus(reason, true);
    closeCamera();
    return;
  }
  try {
    cam.stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
      audio: false,
    });
    $("camVideo").srcObject = cam.stream;
    await $("camVideo").play().catch(() => {});
  } catch (e) {
    // 权限被拒或无摄像头：不要静默失败。提示写进状态栏——浮层马上就关了，
    // 只写在浮层里用户根本来不及看。
    const reason = `无法打开相机（${e.name || e.message}），请使用文件选择器`;
    $("camHint").textContent = reason;
    setStatus(reason, true);
    setTimeout(closeCamera, 900);
  }
}

/* 停轨必须跟着"这一层真的收起了"走，而不是跟着某个按钮：权限被拒时的延时关闭、
   返回键、Esc、点「取消」四条路都得把摄像头指示灯关掉，漏一条就是一直亮着。 */
function hideCamera() {
  if (cam.stream) {
    cam.stream.getTracks().forEach((t) => t.stop());
    cam.stream = null;
  }
  $("cameraModal").classList.add("hidden");
}

function closeCamera() { Layers.close("camera"); }

function shootPhoto() {
  const video = $("camVideo");
  const canvas = $("camCanvas");
  if (!video.videoWidth) { $("camHint").textContent = "相机还没准备好，稍等一下再拍"; return; }
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  canvas.toBlob((blob) => { cam.blob = blob; }, "image/jpeg", 0.92);
  canvas.classList.remove("hidden");
  video.classList.add("hidden");
  $("camShoot").classList.add("hidden");
  $("camRetake").classList.remove("hidden");
  $("camUse").classList.remove("hidden");
}

function retake() {
  cam.blob = null;
  $("camCanvas").classList.add("hidden");
  $("camVideo").classList.remove("hidden");
  $("camShoot").classList.remove("hidden");
  $("camRetake").classList.add("hidden");
  $("camUse").classList.add("hidden");
}

async function usePhoto() {
  if (!cam.blob) { $("camHint").textContent = "没有拍到内容"; return; }
  const file = new File([cam.blob], `拍照-${Date.now()}.jpg`, { type: "image/jpeg" });
  closeCamera();
  const chip = attachmentChip({ name: file.name, size: file.size, kind: "?" }, true);
  $("attRow").appendChild(chip);
  try {
    const rec = await API.upload(file);
    chip.replaceWith(attachmentChip(rec));
    state.pending.push(rec);
  } catch (e) {
    markChipFailed(chip, file.name, e.message);
  }
  updateSendEnabled();
}

/* ---------------- 侧栏开合 ---------------- */
function openSidebar() {
  $("sidebar").classList.add("open"); $("backdrop").classList.add("show");
  Layers.open("sidebar", hideSidebar);
  /* 他点名的补弹时机：首屏那一弹可能被登录层盖住（.auth 的 z 更高），也可能他正忙着
     打字直接划走了。拉开侧栏是一个"在看界面"的时刻，此时只要内存里已知有新版就再给一次
     机会——注意是 reoffer 而不是 maybeAsk：这里绝不发请求，☰ 一晚上按十次也不多出一次网络。 */
  reofferUpdate();
}
function hideSidebar() { $("sidebar").classList.remove("open"); $("backdrop").classList.remove("show"); }
function closeSidebar() { Layers.close("sidebar"); }

function autosize(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, window.innerHeight * 0.4) + "px";
}

/* ---------------- 按住说话（v0.22，与原生 VoiceUi 同一套交互） ----------------
 * 窄屏（≤860px，和 Enter 键/placeholder 同一条分界）且输入框为空时长按起说：
 * Web Speech API 实时转写，松手发送（走和表单同一个 send()），上滑 100px 进
 * 取消区再松手即丢弃。桌面鼠标不接管；浏览器没有识别引擎时长按只说实话。 */
const VOICE_SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const voice = { armed: false, active: false, cancelling: false,
                text: "", timer: 0, rec: null, downX: 0, downY: 0 };

function voiceShow(cancelling) {
  const mask = $("voiceMask");
  mask.classList.remove("hidden");
  mask.classList.toggle("cancelling", cancelling);
  $("voiceHint").textContent = cancelling ? "松开取消" : "松手发送，上移取消";
}
function voiceHeard(text) {
  const el = $("voiceHeard");
  el.textContent = text;
  el.classList.toggle("hidden", !text);
}
function voiceHide() {
  $("voiceMask").classList.add("hidden");
  $("voiceMask").classList.remove("cancelling");
  voiceHeard("");
}
function voiceStopEngine() {
  const rec = voice.rec;
  voice.active = false; voice.rec = null;
  if (rec) { try { rec.abort(); } catch (e) {} }
  voiceHide();
}

function voiceStart() {
  if (!VOICE_SR) {
    setStatus("这个浏览器不支持语音输入，换 Chrome / Edge 或系统浏览器试试", true);
    return;
  }
  voice.active = true; voice.cancelling = false; voice.text = "";
  voiceShow(false);
  const rec = new VOICE_SR();
  voice.rec = rec;
  rec.lang = "zh-CN"; rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
  rec.onresult = (ev) => {
    let fin = "", inter = "";
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const r = ev.results[i];
      if (r.isFinal) fin += r[0].transcript; else inter += r[0].transcript;
    }
    voiceHeard(fin || inter);
    if (fin) voice.text = fin; else if (!voice.text) voice.text = inter;
  };
  rec.onerror = (ev) => {
    // aborted 是取消区松手/重开时的自家用语，不外传
    if (ev.error === "aborted") return;
    voiceStopEngine();
    if (ev.error === "not-allowed" || ev.error === "service-not-allowed")
      setStatus("页面要用麦克风，允许后才能识别", true);
    else if (ev.error !== "no-speech") setStatus("没听清，再长按说一次", true);
  };
  rec.onend = () => {
    // 只有"松手待发送、等收尾"这一路还挂着 active；取消与出错都已自行收摊
    if (!voice.active) return;
    const t = (voice.text || "").trim();
    voice.active = false; voice.rec = null; voiceHide();
    if (voice.cancelling) { voice.cancelling = false; return; }
    if (t) send(t); else setStatus("没听到什么，再长按说一次", true);
  };
  try { rec.start(); }
  catch (e) { voiceStopEngine(); setStatus("语音识别没启动成，再长按试一次", true); }
}

function bindVoiceHold() {
  const input = $("input");
  // 波纹条：与原生 WaveBars 同一个权重——sin 包络两头低中间高 + 打散抖动；
  // Web Speech 没有音量回调，律动交给 CSS 动画自己呼吸。
  const bars = $("voiceBars");
  for (let i = 0; i < 30; i++) {
    const b = document.createElement("i");
    const env = Math.sin((i / 29) * Math.PI) * 0.7 + 0.3;
    const seed = Math.abs(Math.sin(i * 12.9898) * 43758.5453) % 1;
    b.style.height = Math.round(100 * Math.min(1, env * (0.4 + 0.6 * seed))) + "%";
    b.style.animationDuration = Math.round(560 + seed * 420) + "ms";
    b.style.animationDelay = Math.round(-seed * 800) + "ms";
    bars.appendChild(b);
  }
  input.addEventListener("pointerdown", (e) => {
    if (window.innerWidth > 860 || e.button > 0) return;   // 桌面、右键盘不接管
    if (input.value.trim()) return;                          // 有字留给编辑，同原生
    if (voice.armed || voice.active) return;
    e.preventDefault();                                      // 压掉聚焦/光标/选字起手
    voice.armed = true; voice.downX = e.clientX; voice.downY = e.clientY;
    voice.timer = setTimeout(() => { voice.timer = 0; voice.armed = false; voiceStart(); }, 260);
    try { input.setPointerCapture(e.pointerId); } catch (err) {}
  });
  input.addEventListener("pointermove", (e) => {
    if (voice.armed &&
        (Math.abs(voice.downY - e.clientY) > 12 || Math.abs(voice.downX - e.clientX) > 12)) {
      clearTimeout(voice.timer); voice.timer = 0; voice.armed = false;  // 先动了=不是按住说话
    }
    if (voice.active) {
      const cancel = voice.downY - e.clientY > 100;
      if (cancel !== voice.cancelling) { voice.cancelling = cancel; voiceShow(cancel); }
    }
  });
  const up = () => {
    if (voice.timer) { clearTimeout(voice.timer); voice.timer = 0; }
    const wasArmed = voice.armed; voice.armed = false;
    if (voice.active) {
      if (voice.cancelling) voiceStopEngine();
      else { try { voice.rec.stop(); } catch (e) { voiceStopEngine(); } }  // 停引擎等最终结果，onend 发
    } else if (wasArmed) {
      input.focus();     // 轻点：pointerdown 被我们压了，聚焦补回来（键盘照常弹）
    }
  };
  input.addEventListener("pointerup", up);
  input.addEventListener("pointercancel", () => { voice.armed = false; if (voice.active) voiceStopEngine(); });
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  $("openSidebar").onclick = openSidebar;
  $("closeSidebar").onclick = closeSidebar;
  $("backdrop").onclick = closeSidebar;
  $("newChatBtn").onclick = () => { newChat(); closeSidebar(); };
  $("navProviders").onclick = () => { openSettings("providers"); closeSidebar(); };
  $("navMemory").onclick = () => { openSettings("memory"); closeSidebar(); };
  // 不传 tab：落在哪一页由 openSettings 按角色决定（管理员=模型服务，其他人=连接）。
  // 收起侧栏是必须的：手机上它是抽屉，不收就是一片遮罩挡在面板前面。
  $("whoRow").onclick = () => { openSettings(); closeSidebar(); };
  $("themeBtn").onclick = () => { pref.theme = pref.theme === "dark" ? "light" : "dark"; applyTheme(); };
  $("sessionSearch").oninput = (e) => { state.filter = e.target.value; renderSessions(); };

  $("modelSel").onchange = (e) => {
    const picked = state.providers.find((p) => p.id === e.target.value);
    if (picked && !picked.usable) {
      setStatus(isAdmin() ? "该模型未配置密钥，请先在「设置 → 模型服务」补全"
                          : "该模型还没配好密钥，请联系管理员处理", true);
      e.target.value = pref.provider;
      return;
    }
    pref.provider = e.target.value;
    // 本机立刻生效，服务端那份让"我的默认"跟着人走而不是跟着设备走。
    // 存失败不反悔本次选择：localStorage 仍是这台设备的答案，下次开机再补写。
    API.setMyDefaultProvider(e.target.value).catch(() => {});
    renderModelChip();   // 发送框旁的芯片跟着换，别留旧名字
    setStatus("");
  };
  $("exportBtn").onclick = exportCurrent;

  $("chatForm").onsubmit = (e) => {
    e.preventDefault();
    const el = $("input");
    const text = el.value;
    el.value = ""; autosize(el);
    send(text);
  };
  $("stopBtn").onclick = stop;

  const input = $("input");
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && window.innerWidth > 860) {
      e.preventDefault();
      $("chatForm").requestSubmit();
    }
  });
  input.addEventListener("input", () => { autosize(input); updateSendEnabled(); });
  // 手机宽度上根本没有 Shift+Enter（上面那颗回车键用的就是同一条 860 分界），
  // 提示不该留着（v0.21 双端同契约）。v0.22 起窄屏真的能按住说话——但只有
  // 浏览器带识别引擎才写这句兑现得了的话；桌面端保留 Shift+Enter 提示。
  input.placeholder = window.innerWidth > 860 ? "发消息，Shift + Enter 换行"
    : (VOICE_SR ? "发消息或按住说话" : "发消息");
  bindVoiceHold();

  $("attachBtn").onclick = (e) => { e.stopPropagation(); toggleAttachMenu(); };
  // 不给它写 setAttachMenu(false)：附件菜单是"让位层"，openCamera 自己会把它连同
  // 那条历史一起换掉。这里再关一次就是同一件事的两个执行者——而且关是异步的
  // （history.go），紧跟着的 pushState 会落错条目，返回键从这一刻起就对不上界面。
  $("pickCamera").onclick = () => openCamera();
  $("pickImage").onclick = () => { setAttachMenu(false); $("imageInput").click(); };
  $("pickFile").onclick = () => { setAttachMenu(false); $("fileInput").click(); };
  document.addEventListener("click", (e) => {
    const menu = $("attachMenu");
    if (!menu.classList.contains("hidden") && !menu.contains(e.target)) setAttachMenu(false);
  });
  // 模型快切芯片：与附件菜单同款纪律——stopPropagation 防"开完立刻被外面点击关掉"，
  // 点弹单外任意处收起。
  $("modelChip").onclick = (e) => {
    e.stopPropagation();
    setModelMenu($("modelMenu").classList.contains("hidden"));
  };
  document.addEventListener("click", (e) => {
    const menu = $("modelMenu");
    if (!menu.classList.contains("hidden") && !menu.contains(e.target) && e.target !== $("modelChip")) {
      setModelMenu(false);
    }
  });
  $("imageInput").onchange = (e) => pickFiles(e.target);
  $("fileInput").onchange = (e) => pickFiles(e.target);

  $("camCancel").onclick = closeCamera;
  $("camShoot").onclick = shootPhoto;
  $("camRetake").onclick = retake;
  $("camUse").onclick = usePhoto;

  $("closeSettings").onclick = closeSettings;
  $("settings").onclick = (e) => { if (e.target === $("settings")) closeSettings(); };
  $("setBack").onclick = closeSetPage;
  $("rowAccounts").onclick = () => openSetPage("accounts");
  $("rowProviders").onclick = () => openSetPage("providers");
  $("rowPersona").onclick = () => openSetPage("persona");
  $("rowMemory").onclick = () => openSetPage("memory");
  $("rowReminders").onclick = () => openSetPage("reminders");
  renderAboutRows();
  /* 底部卡片那三颗。「立即更新」与「稍后」都写当天的日期戳：这一版今天不再弹第二次，
     不管他是点了更新还是点了拒绝——他已经知道有新版了，同一天再问是骚扰。
     那颗 <a> 的 click 只负责收尾（链接自己会跳走），不许 return false 去拦它。 */
  $("updateLaterBtn").onclick = hideUpdateSheet;
  $("updateGoBtn").onclick = () => { SHELL.checkUpdate(); hideUpdateSheet(); };
  $("updateGoLink").onclick = hideUpdateSheet;
  // 改密码复用首层那套三步找回：这里再放一份字段就是第二个要各自校验、
  // 各自挡双击、各自跟后端字段名对齐的地方。showAuthView 只在那层可见时换表单，
  // 所以先把层打开，再翻到找回那张。
  $("rowPassword").onclick = () => { closeSettings(); showAuth(); showAuthView("recover"); };
  $("rowLogout").onclick = logoutCurrent;

  $("addProviderBtn").onclick = () => openBlankProviderForm("mine");
  $("addSharedBtn").onclick = () => openBlankProviderForm("admin");
  $("provForm").onsubmit = saveProvider;
  $("provTestBtn").onclick = testProviderDraft;
  $("provCancelBtn").onclick = closeProviderForm;

  $("ctxRange").oninput = (e) => {
    pref.contextTokensK = e.target.value;
    $("ctxVal").textContent = pref.contextTokensK;
  };

  $("memoryAddForm").onsubmit = async (e) => {
    e.preventDefault();
    const el = $("memoryInput");
    if (!el.value.trim()) return;
    try { await API.addMemory(el.value.trim()); el.value = ""; loadMemories(); }
    catch (err) { setStatus("添加记忆失败：" + err.message, true); }
  };
  $("memorySearchForm").onsubmit = (e) => {
    e.preventDefault();
    state.memoryQuery = $("memoryQuery").value.trim();
    loadMemories();
  };

  $("savePersonaBtn").onclick = () => {
    pref.setPersona(pref.sessionId, $("personaInput").value.trim());
    syncPersonaChip(); closeSettings(); setStatus("角色设定已保存");
  };
  $("clearPersonaBtn").onclick = () => {
    pref.setPersona(pref.sessionId, ""); syncPersonaChip(); setStatus("角色设定已清除");
  };

  $("saveTokenBtn").onclick = async () => {
    const token = $("tokenInput").value.trim();
    if (!token) { setStatus("令牌那一格还是空的", true); return; }
    $("saveTokenBtn").disabled = true;
    try {
      // 手工录入的凭据走 adopt：明文只活过这一次请求头，换回的是 httpOnly Cookie
      // 与服务端报回的真实身份。旧写法拿 "manual" 占位再等 loadWho 补名字，
      // 现在不必——adopt 的响应体就是 /v1/auth/me 同形的答案。
      const me = await API.adopt(token);
      $("tokenInput").value = "";          // 用完就清出输入框，不留第二眼
      addIdentity({ user_id: me.user_id, username: me.username, role: me.role });
      location.reload();   // 令牌换了就是换了人（重跑 boot 会重复绑定事件）
    } catch (e) {
      $("saveTokenBtn").disabled = false;
      setStatus("这枚凭据没被认：" + e.message, true);
    }
  };
  $("openRegister").onclick = () => { closeSettings(); showAuth("register"); };
  $("authEye").onclick = toggleAuthPass;
  $("authForgot").onclick = () => showAuthView("recover");
  // 回去登录走 showAuth 这个收口，不是只把两块表单的 class 换一换：猜错 401 之后
  // 离开这条流程的人，三句找回答案与新密码得跟着清出格子（见 clearAuthCredentials）
  $("rcBack").onclick = () => showAuth("login");
  // 填错答案不该逼人重开整张表单：退回第一步，清掉第二步留下的那两处红字（第一步
  // 没有那些格子，红字跟过来就是指着一屏已隐藏的东西），焦点落回第一步里他最后填过
  // 的那一格——确认密码
  $("regBack").onclick = () => {
    regStep = 1;
    authFail("");
    setUserError("");
    renderRegister();
    $("authPass2").focus();
  };
  $("recoverForm").onsubmit = (e) => { e.preventDefault(); submitRecovery(); };
  $("authSwitch").onclick = () => setAuthMode(authMode === "register" ? "login" : "register");
  // 提交挂在 form 上而不是某个按钮上：两个框里按回车都该等于点主按钮。
  // 第一步/第二步的分流在 submitAuth 与 submitRecovery 的最前面，不在这里。
  $("authForm").onsubmit = (e) => { e.preventDefault(); submitAuth(); };
  renderRecoveryQuestions();

  /* Esc 与手机的返回键是同一个动作：退掉最上面一层。写成两段（各自判断该关哪个）就是
     两份真相——相机已经改成"关掉那一层时停轨"之后，这里漏掉一层不会报错，只会让
     指示灯一直亮着。顺序、让位、多步回退全在 layers.js 那一处算。 */
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    Layers.closeTop();
  });
}

async function exportCurrent() {
  /* 导出不再在本地拼 blob：壳那边的 WebView 收不到 blob 的下载回调，也绝不会给
     下载请求带上 Authorization 头。改成先向服务端换一张一次性票据，再把浏览器/壳
     直接指到那个真实链接上——响应带 Content-Disposition，下载自然发生。 */
  if (!state.messages.some((m) => !m.transient)) { setStatus("当前没有可导出的对话", true); return; }
  try {
    const t = await API.exportTicket(pref.sessionId);
    location.href = t.path;
  } catch (e) {
    setStatus("导出失败：" + e.message, true);
  }
}

/** 移动端软键盘会盖住输入框：把 body 高度收到可视视口，flex 布局即整体让位。 */
function setupKeyboardAware() {
  const vv = window.visualViewport;
  if (!vv || window.innerWidth > 860) return;

  // visualViewport 的 resize/scroll 是成串到来的，每次都直接写 body 高度
  // 会强制同步布局：用 rAF 合并成每帧最多一次（兜底风格同 runStream）。
  const raf = window.requestAnimationFrame || function (fn) { return setTimeout(fn, 32); };
  let pending = 0;
  const apply = () => {
    pending = 0;
    document.body.style.height = `${Math.round(vv.height)}px`;
    if (document.activeElement === $("input")) scrollBottom();
  };
  const schedule = () => { if (!pending) pending = raf(apply); };
  vv.addEventListener("resize", schedule);
  vv.addEventListener("scroll", schedule);
  // focus 那一下也走 schedule：键盘弹起的 resize 事件与这次补跑会撞在同一帧，
  // 各自直接 apply 就是同帧双写 body 高度；进 rAF 合并路径后一帧最多执行一次，
  // setTimeout 仍保证至少补跑一次（rAF 不触发的老 WebView 上兜底路径也在里面）。
  $("input").addEventListener("focus", () => setTimeout(schedule, 120));
  $("input").addEventListener("blur", () => {
    setTimeout(() => { document.body.style.height = ""; }, 150);
  });
}

/* ---------------- 启动 ---------------- */
/* 读回这一条指针指向的历史。返回 null = 没什么要交代的；返回一个 Error = "这次没读回
   历史"，但**不自己写状态条**，交给 loadServerData 在三路都落定之后统一写。
   理由见那里：并发之后状态条是谁后回来谁抢，自己写就可能被 loadModels 那句
   setStatus("") 擦掉，症状从"空聊天区 + 一句实话"退化成"空聊天区 + 什么都没有"。 */
async function restore() {
  // 没有指针不等于"没什么可做的"：那正是上一个人的对话该消失的时刻。
  // 原先这里直接 return，靠"拿旧 id 去 GET 会撞 404"才把消息清掉——那是运气。
  if (!pref.sessionId) { state.messages = []; return null; }
  try {
    const full = await API.getSession(pref.sessionId);
    state.messages = (full.messages || []).map((m) => ({
      role: m.role, content: m.content, message_id: m.message_id, attachments: m.attachments,
    }));
    await hydrateImageUrls(state.messages.flatMap((m) => m.attachments || []));
  } catch (e) {
    // 指针指的这条会话在服务端已经没有了：归零指针，界面按"新对话"走，不是一句错误。
    if (e.status === 404) { pref.sessionId = ""; state.messages = []; return null; }
    return e;
  }
  return null;
}

/** 服务端数据的四步：注册成功、令牌变更后都要原样重跑一遍，不能只活在 boot 里。
 *
 * 前三步并发、第四步排最后（2026-09-22：用户报"每次重新打开网页版都要等很久才出
 * 对话界面"，分段量下来源站本机 /health p50 7ms，慢的是**趟数 × 每趟的隧道往返**，
 * 单趟 ttfb 300~430ms。旧的写法是 loadModels → loadSessions → restore 一路 await
 * 一路，光这三趟就是 1 秒左右的纯等待）。
 *
 * 并发前逐一查过的先后依赖（结论：三路彼此不相干，只有 ensureSession 真排在后面）：
 * - loadModels()：输入只有 /v1/models 那个响应；写 state.providers / serverDefault /
 *   presets，并在本机存的 pref.provider 已不可用时换成服务端默认；读 state.me
 *   （isAdmin()，零模型时按角色分流提示文案）——那是 loadWho 的产物，loadWho 仍第一。
 * - loadSessions()：写 state.sessions 并重画侧栏；renderSessions() 读 pref.sessionId
 *   只为标"当前这一条"，而 pref.sessionId 来自本机存储，在 loadWho 之前就定了。
 * - restore()：GET 的 id 也来自本机的 pref.sessionId，用不到 loadModels 带回来的
 *   provider——**这是本轮唯一一处"看起来要等 models、其实不用"**：它写进的
 *   state.messages 只被 messageNode() 消费，那里读的是 m.role / m.content 和每条
 *   附件自己的 a.kind / a.url（supports_vision 只出现在模型下拉的文案里，不参与
 *   渲染判断），三路里没有一处把 providers 喂给它。
 * - ensureSession()：两处硬依赖留到最后——判据 `!pref.sessionId && currentProvider()`
 *   里的 currentProvider() 读的是 loadModels 纠正之后的 pref.provider，函数体第一句
 *   `state.sessions.some(...)` 读的是 loadSessions 的结果。并发就并发在这三步。
 *
 * 等的是"全部落定"（Promise.allSettled）而不是"第一个坏消息"（Promise.all）：
 * Promise.all 一有人 reject 就立刻返回，剩下那几路还在跑却再没人等，boot 会拿着
 * 半空的状态渲染完，晚到的 restore() 把 state.messages 填上时已经没人重画了——
 * "会话列表好了、聊天区一直空着、也不报错"就是这么来的。allSettled 让每一路都跑到
 * 自己的终点，再把第一个失败按数组顺序（models → sessions → restore，与旧串行
 * 一致）重新抛给 boot 那个 needsAuth(e) 出口：出口只有一个，几路失败都是它。
 * 已经改写成功的 state 不回滚：谁坏了说谁，其余照旧露出来。 */
async function loadServerData() {
  const [models, sessions, history] = await Promise.allSettled([
    loadModels(), loadSessions(), restore(),
  ]);
  const lost = history.status === "fulfilled" ? history.value : null;
  if (lost && !needsAuth(lost)) setStatus("会话加载失败：" + lost.message, true);
  const first = [models, sessions, history].find((r) => r.status === "rejected");
  if (first) throw first.reason;
  if (!pref.sessionId && currentProvider()) await ensureSession();
}

/* HTML 允许缓存 30 秒（backend/app/web/web_router.py 的 PAGE_CACHE），于是"旧页面配
   新脚本"有一个窗口，症状是缺元素、点了没反应。判断单独成函数，是为了让它在 node 里
   真跑得起来（tests/test_web_pwa.py 的 _BOOT_JS_HARNESS 同一套路）。
   缺任何一边都不跳：没登录时拿不到服务端那一版，而这次部署之前留下的旧 HTML 压根没有
   window.__ASSETS__——那种情况下瞎跳只会把人反复踢回登录页。 */
function staleBuild(local, server) {
  return Boolean(local) && Boolean(server) && local !== server;
}

/* "现在线上是哪一版"问 sw.js：它是全站唯一一个刻意不缓存的文件，内容里就带着当下的
   水印。排在 boot 最后发，不挡首屏、也不进任何一条等待链。跳过去的那一个地址带上
   ?b=<水印>，等于换一个没人缓存过的 URL，同时给"下一趟还是旧的"留一个止损点。 */
function checkBuild(local) {
  fetch("sw.js?build-check=" + Date.now(), { cache: "no-store" })
    .then((res) => res.text())
    .then((text) => {
      const live = (text.match(/const V = "([^"]+)"/) || [])[1];
      const jumped = new URLSearchParams(location.search).get("b");
      if (!staleBuild(local, live) || jumped === live) return;
      location.replace(location.pathname + "?b=" + encodeURIComponent(live));
    })
    .catch(() => { /* 问不到就不动：这一趟不值得让界面变红 */ });
}

async function boot() {
  // 必须排第一：升级（老键/清单里的明文洗成 Cookie）没做完就往下读，等于把
  // 一个还在线的人当陌生人，或者让旧明文多活一个页面生命周期。
  await upgradeIdentitiesToCookie();
  applyTheme();
  bind();
  setupKeyboardAware();
  // 壳的事件入口只注册这一次。没有桥时这个数组永远没人推，注册本身无害。
  SHELL.onEvent(onShellEvent);
  updateSendEnabled();
  migrateContextPref();
  syncCtxRow();   // 量程/上限先按兜底画一版，loadModels 后 renderModelSelect 会再校准
  $("connInfo").textContent = location.host;   // 这一页生命周期内的常量，不必等人进账户页才写

  /* 第一屏只能是中性层：方案 C 起本机连"有没有凭据"都看不见（httpOnly 的本意），
     "还有效吗"更必须问服务端一趟（走隧道 0.5~2 秒）。原先按有没有令牌分流，存过令牌的人依然
     先看到空聊天外壳加一个空白模型框，等 401 回来才弹层——手机上那个
     「1 → 3 → 2」的闪序就是它。现在不问完不露任何东西。 */
  showAuthPending();
  let unreachable = false;
  try {
    await loadWho();          // 先知道自己是谁：角色决定下面哪些面存在、哪些按钮该收起来
    await loadServerData();
  } catch (e) {
    if (!needsAuth(e)) {
      unreachable = true;
      setStatus("后端连接失败：" + e.message, true);
    }
  }
  // 认不出人才挡屏。连不上（503/断网）时弹一个只会失败的登录框，等于把
  // "服务没起来"伪装成"你没登录"——所以那一路露出外壳和上面那句话。
  if (state.me) hideAuth();          /* 令牌有效，含冷启动先盖了中性层那一路 */
  else if (unreachable) hideAuth();
  else showAuth(currentEntry() ? "login" : "register");   /* 记着人却被拒才换登录面；谁的会话都没有的仍停在注册 */
  renderMessages();
  syncPersonaChip();

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js", { scope: "./" }).catch(() => {});
  /* 冷启动时壳里可能已经躺着一件"分享 → AI 助手"送进来的文件（人在没打开网页时就分享了）。
     排在这一段最后：上传要带令牌，认不出人时传上去只会 401。没有桥它第一句就 return。 */
  if (state.me) drainShares();
  /* 「发现版本更新」排在最后一句：它既不阻塞首屏也不分登录态（端点免鉴权，版本号是壳
     报的，不是令牌的属性）。没有桥时 maybeAskUpdate 第一句就 return，浏览器里连一次
     fetch 都不会发出去。 */
  maybeAskUpdate();
  checkBuild(window.__ASSETS__);
}

document.addEventListener("DOMContentLoaded", boot);
