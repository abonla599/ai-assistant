package xyz.fenever.assistant.core;

import java.util.regex.Pattern;

/**
 * id 白名单（spec §2 铁律②）。全工程只有这一处定义这个正则——
 * 桥、分享队列、事件构造器都从这里取，避免某一处悄悄放宽成能被拼进路径或 JS 的形状。
 *
 * <p>提醒的 id 由 JS 生成（{@code r-} + 12 位小写字母数字），分享的 id 由壳生成
 * （16 位 url-safe）。两者都必须过这里。
 */
public final class IDs {

    public static final Pattern PATTERN = Pattern.compile("^[A-Za-z0-9_-]{8,24}$");

    private IDs() {}

    /** null、空串、带路径分隔符、长度不对一律 false。 */
    public static boolean valid(String id) {
        return id != null && PATTERN.matcher(id).matches();
    }
}
