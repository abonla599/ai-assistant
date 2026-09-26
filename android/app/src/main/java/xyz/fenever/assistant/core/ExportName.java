package xyz.fenever.assistant.core;

import java.util.regex.Pattern;

/**
 * 会话导出文件名的唯一一份客户端口径（v0.23 T2.4）。
 *
 * <p>为什么需要这个类：网页版把 {@code location.href} 指到一次性票据链接，文件名由
 * 服务端 {@code Content-Disposition} 说了算；原生走 DownloadManager 必须自己给出
 * 落盘文件名。直接把后端 {@code export_store._safe_filename} 的规则在 Kotlin 里
 * 手抄一遍，就是养出"第二个事实来源"——哪天服务端改了清洗规则，两端文件名悄悄分叉，
 * 没有任何测试会红。所以规则收在这里：纯 JVM、无 android 依赖，双壳字节一致拷贝，
 * 台架与后端钉死测试两头各数各的（同 {@link ReleasePlan} 与 build.gradle 的接缝检查）。
 *
 * <p>镜像对象的逐字行为（backend/app/session/export_store.py）：
 * <ul>
 *   <li>{@code re.sub(r'[\\/:*?"<>|\r\n]+', " ", text or "")} —— 连续禁用字符合并成一个空格；</li>
 *   <li>{@code .strip()} —— Python 的空白集是 Unicode White_Space 全量（含 NBSP、
 *       全角空格 U+3000、NEL U+0085），Java 的 {@code String.strip()} 不认其中三个，
 *       所以下面用显式码点表逐一对位，不借道；</li>
 *   <li>{@code (cleaned or "对话")[:40]} —— Python 切片按码点，Java
 *       {@code substring} 按 UTF-16 码元：标题里一个 emoji 就能把 40 的刀口切到
 *       代理对中间。这里用 codePoint API 保证两边落在同一个字符上。</li>
 * </ul>
 */
public final class ExportName {

    private ExportName() {
    }

    /** 服务端内容头给的固定回退名之外的扩展名（content_disposition 拼的是 name + ".md"）。 */
    static final String MARKDOWN_EXT = ".md";

    /** 清洗后为空的兜底名。与 _safe_filename 的 "对话" 逐字同值。 */
    static final String EMPTY_FALLBACK = "对话";

    /** 文件名上限（码点数）。与 [:40] 同值。 */
    static final int NAME_LIMIT = 40;

    private static final Pattern FORBIDDEN_RUNS =
            Pattern.compile("[\\\\/:*?\"<>|\\r\\n]+");

    /**
     * 票据路径的形状门（isTicketPath）。生成侧是 {@code secrets.token_urlsafe(16)}，
     * 产物恰好 22 个 base64url 字符——这个 22 不是手抄装饰：后端钉死测试
     * （test_release_probe）拿 {@code TICKET_BYTES} 实算出的长度对这里的字面量，
     * 哪天生成侧改字节数，钉死先红，而不是原生悄悄拒绝真链接。
     */
    static final String TICKET_PATH_PREFIX = "/v1/exports/";
    private static final Pattern TICKET_PATH =
            Pattern.compile("^/v1/exports/[A-Za-z0-9_-]{22}$");

    /** 镜像 _safe_filename：禁用字符合并成空格 → 按 Unicode White_Space 剥边 → 空则兜底 → 40 码点截断。 */
    public static String safeFilename(String text) {
        String cleaned = FORBIDDEN_RUNS.matcher(text == null ? "" : text).replaceAll(" ");
        cleaned = pyStrip(cleaned);
        if (cleaned.isEmpty()) {
            cleaned = EMPTY_FALLBACK;
        }
        return limitCodePoints(cleaned, NAME_LIMIT);
    }

    /** 兑换落盘用的文件名：与服务端 Content-Disposition 的 filename* 同名同规则（不带 URL 编码）。 */
    public static String exportFileName(String sessionTitle) {
        return safeFilename(sessionTitle) + MARKDOWN_EXT;
    }

    /**
     * 票据路径必须是「前缀 + 恰好一段 22 字符票据形状」。
     *
     * <p>DownloadManager 会替我们记住这条 URL（系统下载队列），所以入队前必须亲手
     * 确认服务端 path 字段没有夹带越境的绝对地址、锚点或多出来的段——网页版靠
     * 同源相对路径天然成立，原生没有这层浏览器护栏，只能自己把门关上。
     */
    public static boolean isTicketPath(String path) {
        return path != null && TICKET_PATH.matcher(path).matches();
    }

    // ---------- Python 语义的两个小零件 ----------

    /** Python str.isspace() 的码点集（Unicode White_Space），逐字符对位，不用 Java 的近似。 */
    private static boolean isPySpace(int codePoint) {
        return (codePoint >= 0x09 && codePoint <= 0x0D)
                || (codePoint >= 0x1C && codePoint <= 0x1F)
                || codePoint == 0x20 || codePoint == 0x85 || codePoint == 0xA0
                || codePoint == 0x1680
                || (codePoint >= 0x2000 && codePoint <= 0x200A)
                || codePoint == 0x2028 || codePoint == 0x2029
                || codePoint == 0x202F || codePoint == 0x205F || codePoint == 0x3000;
    }

    private static String pyStrip(String s) {
        int start = 0;
        int end = s.length();
        while (start < end) {
            int cp = s.codePointAt(start);
            if (!isPySpace(cp)) {
                break;
            }
            start += Character.charCount(cp);
        }
        while (end > start) {
            int cp = s.codePointBefore(end);
            if (!isPySpace(cp)) {
                break;
            }
            end -= Character.charCount(cp);
        }
        return s.substring(start, end);
    }

    private static String limitCodePoints(String s, int limit) {
        if (s.codePointCount(0, s.length()) <= limit) {
            return s;
        }
        return s.substring(0, s.offsetByCodePoints(0, limit));
    }
}
