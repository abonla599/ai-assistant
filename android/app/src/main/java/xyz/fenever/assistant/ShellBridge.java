package xyz.fenever.assistant;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.util.Base64;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import xyz.fenever.assistant.core.ChunkRequest;
import xyz.fenever.assistant.core.IDs;
import xyz.fenever.assistant.core.MiniJson;
import xyz.fenever.assistant.core.Reminder;
import xyz.fenever.assistant.core.ReminderDraft;
import xyz.fenever.assistant.core.ReminderStore;
import xyz.fenever.assistant.core.ShareInbox;
import xyz.fenever.assistant.core.ShellEvents;

/**
 * 唯一注入 JS 世界的对象（{@code window.AssistantShell}）。八个方法、一字不差、
 * 全部同步返回一段 JSON 字符串（spec §2）：
 *
 * <pre>
 * capabilities()  setOwner(user)  scheduleReminder(json)  cancelReminder(id)
 * listReminders()  pendingShares()  readShareChunk(json)  consumeShare(id)
 * </pre>
 *
 * 三条铁律逐条落在这里：
 * 1. 不接触会话令牌、不回显任何外部文本，原生到 JS 只有一个固定句式（见 {@link #deliverNow()}），
 *    且句式里只放 id（{@link ShellEvents} 负责保证这点）。
 * 2. id 走 {@link IDs} 白名单；路径由 {@link ShareInbox} 自己拼。
 * 3. {@code readShareChunk} 单次 512KB（{@link ChunkRequest} 夹裁）。
 *
 * <p><b>每次调用先过 {@link #sameOrigin()}</b>。注意它只看主文档 URL，挡不住同源页面里
 * 插入的第三方 frame——那一路由 {@code /app} 的 CSP {@code frame-src 'none'} 从 web 侧堵
 * （spec §2 裁决②）。
 *
 * <p>桥方法跑在 WebView 的 JS 桥线程上，不是主线程：这里的状态全部 {@code synchronized}
 * 或 {@code volatile}，凡是要碰 Activity/WebView 界面的（发事件、要运行时权限）都 {@code post}
 * 回主线程。
 */
public final class ShellBridge {

    /** 与 MainActivity 里其它 requestCode 错开：1001 文件选择、1002 相机。 */
    public static final int NOTIFICATION_PERMISSION_CODE = 1003;

    /**
     * 唯一可信主机名：不写字面量，直接从 {@code BuildConfig.APP_URL} 派生。
     *
     * <p>已逐字符核对过四处：android/app/build.gradle 的 APP_URL、计划 Task 5 示例里的 HOSTS、
     * spec §9 第 8 条、MainActivity 顶部注释，主机名<b>一致</b>，都是 ai.fenever.xyz
     * （任务书里怀疑的「计划把域名写错」经核对不成立，那处字面量本来就是对的）。
     *
     * <p>那为什么仍不写死：这个字符串一旦在桥里独立存在，换域名就得同步两处，
     * 而漏掉的后果不是崩溃也不是编译报错，是 sameOrigin() 恒为 false、桥对每次调用都回
     * origin 拒绝、界面上「就是没有提醒功能」——最难查的那种错。派生自同一个常量就没这个
     * 可能；万一 APP_URL 畸形到取不出主机名，ALLOWED_HOST 为 null，sameOrigin 也只是
     * 恒为 false，宁可全拒。
     */
    private static final String ALLOWED_HOST = Uri.parse(BuildConfig.APP_URL).getHost();

    private static final int MAX_OWNER_LENGTH = 64;

    /** 到点通知与分享事件要推给「还活着的那个网页」，而发通知的是另一个组件。 */
    private static volatile ShellBridge live;

    private final Activity activity;
    private final WebView webview;
    private final ReminderStore reminders;
    private final ShareInbox shares;

    /** 主文档 URL 的快照：桥线程上不直接读 WebView，见 {@link #currentUrl()}。 */
    private volatile String pageUrl;
    /** 只在主线程读写（WebViewClient 的回调与 deliverNow 都在主线程）。 */
    private boolean pageReady;
    private final List<String> queue = new ArrayList<>();

    public ShellBridge(Activity activity, WebView webview, ReminderStore reminders, ShareInbox shares) {
        this.activity = activity;
        this.webview = webview;
        this.reminders = reminders;
        this.shares = shares;
        live = this;
    }

    /** Activity 销毁时调用，免得静态引用把整个 WebView 钉在内存里。 */
    public void release() {
        if (live == this) live = null;
    }

    /** 给 ReminderReceiver / ShareActivity 用：payload 由 ShellEvents 造，已含白名单校验。 */
    public static void publish(String payload) {
        ShellBridge bridge = live;
        if (bridge != null) bridge.event(payload);
    }

    // ---------------------------------------------------------------- 页面状态

    public void notePageStarted(String url) {
        if (url != null) pageUrl = url;
        webview.post(() -> pageReady = false);          // 开始跳转，之前排的事件不能往旧页面发
    }

    public void notePageFinished(String url) {
        if (url != null) pageUrl = url;
        webview.post(() -> {
            pageReady = true;
            deliverNow();                                // 冷启动排下来的事件在这里冲出去
        });
    }

    /** 冷启动带来的 extra（pending_share / open_from）转成事件；非法输入静默丢。 */
    public void queueStartupEvent(Intent intent) {
        if (intent == null) return;
        event(ShellEvents.share(intent.getStringExtra("pending_share")));
        // open_from 有两种形状：通知/桌面组件用 extra（Java 侧建的 PendingIntent，想放什么都有），
        // 静态快捷方式只能放 android:data（见 res/xml/shortcuts.xml 的注释）。两种都折进
        // 同一个白名单，谁也不给第二条放行规则。
        String open = ShellEvents.fromOpenFrom(intent.getStringExtra("open_from"));
        if (open == null) open = ShellEvents.fromOpenUri(intent.getDataString());
        event(open);
    }

    /** 页面没加载完就 evaluateJavascript 会静默丢失，所以先攒着（Task 5 的「排队」）。 */
    public void event(String payload) {
        if (payload == null || payload.isEmpty()) return;
        synchronized (queue) {
            queue.add(payload);
        }
        webview.post(this::deliverNow);
    }

    /** 唯一一处原生到 JS 的写法：固定句式 + 只含 type/id 的 payload。 */
    private void deliverNow() {
        if (!pageReady) return;
        List<String> batch;
        synchronized (queue) {
            if (queue.isEmpty()) return;
            batch = new ArrayList<>(queue);
            queue.clear();
        }
        for (String payload : batch) {
            webview.evaluateJavascript(
                    "window.__shellEvent && window.__shellEvent('" + payload + "')", null);
        }
    }

    // ---------------------------------------------------------------- 铁律 1：origin

    /**
     * 主文档 URL：优先用 WebViewClient 回调缓存的那份（那是在主线程读的），
     * 一次回调都还没收到时才退回去读 WebView.getUrl()。
     * 退回去那一下是在桥线程上访问 WebView 状态，所以拿异常兜住——宁可回绝也不崩。
     */
    private String currentUrl() {
        String cached = pageUrl;
        if (cached != null) return cached;
        try {
            return webview.getUrl();
        } catch (RuntimeException e) {
            return null;
        }
    }

    private boolean sameOrigin() {
        String raw = currentUrl();
        if (raw == null) return false;                   // 连地址都不知道就不给（fail-closed）
        Uri uri = Uri.parse(raw);
        String host = uri.getHost();
        // 只认 https + 我们自己的主机名；断网页 loadDataWithBaseURL(null,...) 之后
        // 主机名是 null，于是桥在离线页上也回绝，这是对的。
        return host != null
                && "https".equalsIgnoreCase(uri.getScheme())
                && host.equalsIgnoreCase(ALLOWED_HOST);
    }

    // ---------------------------------------------------------------- 八个方法

    /**
     * 碰表之前先重读一次。
     *
     * <p>提醒表有两个写者：这里（网页的增删）与 {@code ReminderReceiver}（到点推进），
     * 各自 new 一个 {@link ReminderStore}，而每次写都是整块覆盖。接收器把某条 once 删掉之后，
     * Activity 手里那份还留着——下一次 add/cancel 就把它写回盘上，同一条提醒能反复响。
     * 同进程内 SharedPreferences 的读是内存缓存，所以重读很便宜（表上限 32 条），
     * 换来的是「桥看见的永远是最新的表」。
     */
    private void syncReminders() {
        reminders.reload();
    }

    @JavascriptInterface
    public String capabilities() {
        // 不给 {"ok":false,...} 而是 null：shell.js 的 present 由「能不能解析出一个对象」决定，
        // 回绝时报 null 才会让页面走无桥降级，而不是摆出一堆点了没反应的按钮。
        if (!sameOrigin()) return "null";
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("shell", 1L);
        return MiniJson.encode(out);
    }

    @JavascriptInterface
    public String setOwner(String user) {
        if (!sameOrigin()) return refused("origin");
        String owner = (user == null) ? "" : user.trim();
        if (owner.length() > MAX_OWNER_LENGTH) return refused("too_long");
        syncReminders();
        // 空串 = 清空归属，于是提醒与分享件对谁都不可见（登录态没了就该这样）
        reminders.setOwner(owner);
        shares.setOwner(owner);
        // 组件的内容是按 owner 过滤的，换人不刷就等于上一个人的"今日"还挂在桌面上
        // （spec §9 第 7 条要验的正是这一条）。计划只列了增/删/推进三处，漏了这一处。
        AssistantWidget.refresh(activity);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", Boolean.TRUE);
        return MiniJson.encode(out);
    }

    @JavascriptInterface
    public String scheduleReminder(String payload) {
        if (!sameOrigin()) return refused("origin");
        ReminderDraft draft = ReminderDraft.parse(payload);
        if (draft == null) return refused("bad-payload");
        syncReminders();
        String owner = reminders.activeOwner();
        if (owner == null) return refused("no-owner");   // 没 setOwner 就不许往表里写东西

        Reminder reminder = draft.toReminder(owner);
        if (!reminders.add(reminder)) return refused("too_many");   // 每 owner 32 条，见 ReminderStore
        ReminderScheduler.schedule(activity, reminder);
        askNotificationPermissionOnce();
        AssistantWidget.refresh(activity);              // 计划 Step 4 的三处之一：新增

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", Boolean.TRUE);
        out.put("nextAt", reminder.at);
        return MiniJson.encode(out);
    }

    @JavascriptInterface
    public String cancelReminder(String id) {
        if (!sameOrigin() || !IDs.valid(id)) return refused("origin");
        syncReminders();
        Reminder existing = reminders.byId(id);
        if (existing != null && !belongsToActiveOwner(existing.owner)) {
            // 别人的提醒：连闹钟都不撤，只回「没有这条」，不给探测信号
            return done(false);
        }
        boolean removed = reminders.cancel(id);
        ReminderScheduler.cancel(activity, id);
        AssistantWidget.refresh(activity);              // 计划 Step 4 的三处之二：删除
        return done(removed);
    }

    @JavascriptInterface
    public String listReminders() {
        if (!sameOrigin()) return "[]";
        syncReminders();
        List<Object> rows = new ArrayList<>();
        for (Reminder r : reminders.list()) {             // list() 已按 activeOwner 过滤
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", r.id);
            row.put("at", r.at);
            row.put("title", r.title);
            row.put("repeat", r.repeat);
            rows.add(row);                                // 不含 body：列表页用不到正文
        }
        return MiniJson.encode(rows);
    }

    @JavascriptInterface
    public String pendingShares() {
        if (!sameOrigin()) return "[]";
        return MiniJson.encode(new ArrayList<Object>(shares.pending()));
    }

    /**
     * 参数是【一段 JSON】：{@code {"id":...,"offset":...,"length":...}}。
     * spec §2 初稿写成三个位置参数，@JavascriptInterface 那边我们只给它一个 String。
     */
    @JavascriptInterface
    public String readShareChunk(String payload) {
        if (!sameOrigin()) return emptyChunk();
        ChunkRequest request = ChunkRequest.parse(payload);
        if (request == null) return emptyChunk();
        if (!isVisibleShare(request.id)) return emptyChunk();     // 归属没到期的那份才给读
        byte[] bytes = shares.chunk(request.id, request.offset, request.length);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("b64", Base64.encodeToString(bytes, Base64.NO_WRAP));
        return MiniJson.encode(out);
    }

    @JavascriptInterface
    public String consumeShare(String id) {
        if (!sameOrigin() || !IDs.valid(id)) return refused("origin");
        if (!isVisibleShare(id)) return done(false);
        return done(shares.consume(id));
    }

    // ---------------------------------------------------------------- 小工具

    /**
     * ShareInbox 的 owner 隔离只管【可见性】：{@code chunk}/{@code consume} 在它自己的层里
     * 只认 id（那条 JVM 测试钉死了），所以跨归属的闸门必须在桥这一侧补上——
     * 判据就是「这个 id 在不在 activeOwner 的待分享列表里」。
     */
    private boolean isVisibleShare(String id) {
        for (Map<String, Object> row : shares.pending()) {
            if (id.equals(row.get("id"))) return true;
        }
        return false;
    }

    private boolean belongsToActiveOwner(String owner) {
        String active = reminders.activeOwner();
        return active != null && active.equals(owner);
    }

    /**
     * 第一次设提醒时问一次通知权限（spec §1）。被永久拒绝后再调它只会立刻回调失败、
     * 不再弹框，所以不需要额外的「问过了」标记。
     * requestPermissions 属界面动作，必须回主线程。
     */
    private void askNotificationPermissionOnce() {
        if (activity.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                == android.content.pm.PackageManager.PERMISSION_GRANTED) {
            return;
        }
        activity.runOnUiThread(() -> activity.requestPermissions(
                new String[]{android.Manifest.permission.POST_NOTIFICATIONS},
                NOTIFICATION_PERMISSION_CODE));
    }

    private static String refused(String why) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", Boolean.FALSE);
        out.put("error", why);
        return MiniJson.encode(out);
    }

    private static String done(boolean ok) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", ok ? Boolean.TRUE : Boolean.FALSE);
        return MiniJson.encode(out);
    }

    /** 拒绝与「文件不在了」回同一个形状：JS 那边统一是「拿不到块就提示重新分享」，不解释原因。 */
    private static String emptyChunk() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("b64", "");
        return MiniJson.encode(out);
    }
}
