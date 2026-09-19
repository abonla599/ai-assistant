package xyz.fenever.assistant.core;

/**
 * 原生推给 JS 的那一段 JSON。spec §2 铁律①：整个应用里原生到 JS 只有一个固定句式，
 * 而句式里【只放 id 与固定枚举值】。
 *
 * <p>之所以收在 core 里：这里就是「外部字符串会不会变成一段 JS」的分界线，
 * 值得有一条测试把「带引号或括号的 id 进不来」钉住，而不是靠调用方记得先校验。
 *
 * <p>三个方法都可能在拿不到合法输入时回 {@code null}，调用方据此决定不发这个事件。
 */
public final class ShellEvents {

    public static final String SHARE = "share";
    public static final String REMINDER = "reminder";
    /** 冷启动 extra {@code open_from} 的前缀：点了某条提醒的通知。 */
    public static final String OPEN_FROM_REMINDER = "reminder:";
    /** 快捷入口的两个固定值，白名单之外没有第三种。 */
    public static final String OPEN_CAMERA = "camera";
    public static final String OPEN_NEW_CHAT = "new_chat";

    private ShellEvents() {}

    public static String share(String id) {
        return IDs.valid(id) ? event(SHARE, id) : null;
    }

    public static String reminder(String id) {
        return IDs.valid(id) ? event(REMINDER, id) : null;
    }

    /**
     * 把通知/组件/快捷方式带进来的 {@code open_from} 还原成事件。
     * 只认 {@code reminder:<合法 id>}、{@code camera}、{@code new_chat}，其它回 null。
     */
    public static String fromOpenFrom(String openFrom) {
        if (openFrom == null) return null;
        if (openFrom.startsWith(OPEN_FROM_REMINDER)) return reminder(openFrom.substring(OPEN_FROM_REMINDER.length()));
        if (OPEN_CAMERA.equals(openFrom)) return event("open", OPEN_CAMERA);
        if (OPEN_NEW_CHAT.equals(openFrom)) return event("open", OPEN_NEW_CHAT);
        return null;
    }

    /** type 与 id 都来自本类的方法名与白名单，不含任何外部原文；这里的引号是字面量，不是拼接进来的。 */
    private static String event(String type, String id) {
        return "{\"type\":\"" + type + "\",\"id\":\"" + id + "\"}";
    }
}
