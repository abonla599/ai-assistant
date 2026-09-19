package xyz.fenever.assistant.core;

/**
 * 原生推给 JS 的那一段 JSON。spec §2 铁律①：整个应用里原生到 JS 只有一个固定句式，
 * 而句式里【只放 id 与固定枚举值】。
 *
 * <p>之所以收在 core 里：这里就是「外部字符串会不会变成一段 JS」的分界线，
 * 值得有一条测试把「带引号或括号的 id 进不来」钉住，而不是靠调用方记得先校验。
 *
 * <p>这些构造器都可能在拿不到合法输入时回 {@code null}，调用方据此决定不发这个事件——
 * 传进来的 id / 码 / open_from 全部来自别的进程或别的 App，按不可信输入处理。
 */
public final class ShellEvents {

    public static final String SHARE = "share";
    public static final String REMINDER = "reminder";
    /** 分享被拒：id 那一位只会是 {@link SharePolicy#isCode} 认得的五个固定值之一。 */
    public static final String SHARE_REJECTED = "share_rejected";
    /** 冷启动 extra {@code open_from} 的前缀：点了某条提醒的通知。 */
    public static final String OPEN_FROM_REMINDER = "reminder:";
    /** 快捷入口的两个固定值，白名单之外没有第三种。 */
    public static final String OPEN_CAMERA = "camera";
    public static final String OPEN_NEW_CHAT = "new_chat";
    /** 静态快捷方式带参数的 URI 前缀，见 {@link #fromOpenUri}。 */
    public static final String OPEN_URI_PREFIX = "assistant://open/";

    private ShellEvents() {}

    public static String share(String id) {
        return IDs.valid(id) ? event(SHARE, id) : null;
    }

    public static String reminder(String id) {
        return IDs.valid(id) ? event(REMINDER, id) : null;
    }

    /**
     * 「这条分享我没收下」的事件：拒绝的原因经桥交给网页画，而不是只靠一条 Toast。
     *
     * <p>起因（v0.13 的实机风险点之二）：{@code ShareActivity} 是 {@code Theme.NoDisplay}，
     * 全程没有窗口，而 Android 12 起系统对「没有可见窗口的进程」弹的文字 Toast 会掐
     * （官方 Toast 文档：文字 Toast 只在前台显示）。那正是 spec §9 第 6 条要的
     * "分享 >10MB 有明确拒绝提示"唯一的旧路径，所以补一条不依赖 Toast 的。
     * Toast 本身留着：Android 11 及以下它仍是即时反馈，两条路各不依赖对方。
     *
     * <p>只放 {@link SharePolicy} 枚举出来的固定码，一个外部字符串都不进（铁律①）。
     */
    public static String shareRejected(String code) {
        return SharePolicy.isCode(code) ? event(SHARE_REJECTED, code) : null;
    }

    /**
     * 静态快捷方式那条链路的参数形状：{@code res/xml/shortcuts.xml} 的 {@code <intent>}
     * 只能带 {@code android:data}（那条文件里写了为什么不用嵌套的 {@code <extra>} 标签），
     * 于是那一条入口要先把 URI 折回 {@link #fromOpenFrom} 的输入形状。
     *
     * <p>刻意不另立一套判据：前缀只是把 URI 的外壳剥掉，认不认还是 {@link #fromOpenFrom} 说了算，
     * {@code reminder:<id>} 那一路的 id 也照样过 {@link IDs}。
     *
     * <p>剥外壳时对尾斜杠与 query/fragment 宽容：启动器或 ROM 给 URI 补一个 {@code '/'} 或
     * {@code ?from=icon} 不该让整条快捷方式失效；宽容只发生在"剥掉"这一步，
     * 剥完剩下的那个值仍然只认白名单，所以多一段路径照样是 null。
     */
    public static String fromOpenUri(String dataUri) {
        if (dataUri == null) return null;
        String uri = dataUri.trim();
        if (!uri.startsWith(OPEN_URI_PREFIX)) return null;
        String value = uri.substring(OPEN_URI_PREFIX.length()).trim();
        int cut = value.indexOf('?');
        if (cut < 0) cut = value.indexOf('#');
        if (cut >= 0) value = value.substring(0, cut);
        while (value.endsWith("/")) value = value.substring(0, value.length() - 1);
        return fromOpenFrom(value.isEmpty() ? null : value);
    }

    /** 把通知/组件/快捷方式带进来的 {@code open_from} 还原成事件。
     *  只认 {@code reminder:<合法 id>}、{@code camera}、{@code new_chat}，其它回 null。 */
    public static String fromOpenFrom(String openFrom) {
        if (openFrom == null) return null;
        if (openFrom.startsWith(OPEN_FROM_REMINDER)) return reminder(openFrom.substring(OPEN_FROM_REMINDER.length()));
        if (OPEN_CAMERA.equals(openFrom)) return event("open", OPEN_CAMERA);
        if (OPEN_NEW_CHAT.equals(openFrom)) return event("open", OPEN_NEW_CHAT);
        return null;
    }

    /** 一个值可能写成 {@code open_from} 也可能写成 {@code assistant://open/...}：两种都折一次。 */
    public static String fromLaunchShape(String shape) {
        if (shape == null) return null;
        String direct = fromOpenFrom(shape);
        return direct != null ? direct : fromOpenUri(shape);
    }

    /**
     * 冷启动那条 Intent 的两种形状 → 事件，判据只有 {@link #fromLaunchShape} 这一个。
     *
     * <p>extra 优先于 data：extra 是我们自己那两个组件（通知、桌面按钮）写进
     * {@code PendingIntent} 的，而 {@code android:data} 要经过系统的 XML 解析器，
     * 那条解析器认不认这个属性还没在实机上证实过（见 res/xml/shortcuts.xml 的注释）。
     * 两个都读不是为了放宽——是为了让快捷方式在那条 XML 链路失手时仍然有用。
     */
    public static String fromLaunchExtras(String openFrom, String dataUri) {
        String direct = fromLaunchShape(openFrom);
        return direct != null ? direct : fromLaunchShape(dataUri);
    }

    /** type 与 id 都来自本类的方法名与白名单，不含任何外部原文；这里的引号是字面量，不是拼接进来的。 */
    private static String event(String type, String id) {
        return "{\"type\":\"" + type + "\",\"id\":\"" + id + "\"}";
    }
}
