package xyz.fenever.assistant.core;

import java.security.SecureRandom;
import java.util.Locale;

/**
 * 系统分享入口（spec §4）的判据：哪些件收、多大算太大、给它起什么名字与 id。
 *
 * <p>收在 core 里是因为这三条就是 Task 7 全部的可判定逻辑，而 {@code ShareActivity}
 * 那一侧只剩「查列 → 拷字节 → 交给 {@link ShareInbox}」，在 JVM 里测不了。
 *
 * <p>这里【不】做真正的体积闸门——那在 {@link ShareInbox#put} 里按【实际读到的字节数】把，
 * 因为 Provider 报的 {@code OpenableColumns.SIZE} 可以撒谎或缺失。本类只是拿声明值提前拒绝，
 * 好让入口能给出明确提示（spec §9 第 6 条：>10MB 要"有明确拒绝提示，不是静默没反应"）。
 */
public final class SharePolicy {

    /** 一次分享的处置结果。四种各配一句提示，不共用「失败」这一句。 */
    public enum Intake {
        OK,
        /** 只有 EXTRA_TEXT 而没有 EXTRA_STREAM：这条分享里没有可读取的文件。 */
        NO_STREAM,
        /** 白名单之外的类型：清单里已经只声明了三种，走到这里说明是绕进来的。 */
        UNSUPPORTED_MIME,
        /** 声明体积超过 {@link ShareInbox#MAX_BYTES}。 */
        TOO_LARGE
    }

    /** Provider 查不出体积时的约定值（ContentResolver 对无 SIZE 列的表回 -1）。 */
    public static final long UNKNOWN_SIZE = -1L;

    /**
     * id 字母表只用【小写字母与数字】：{@link IDs} 还放过 {@code -_}，那三种字符在复制、
     * 朗读、某些输入法里最容易变形，而事件那侧要靠 {@code ShellEvents} 把 id 原样带过桥。
     * 16 位 × 36 字母表 ≈ 2^82，随机撞车的可能不必考虑。
     */
    private static final char[] ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789".toCharArray();
    private static final int ID_LENGTH = 16;
    private static final SecureRandom RANDOM = new SecureRandom();

    private SharePolicy() {}

    /** 流已经在手上的情形（ShareActivity 拿到了 EXTRA_STREAM）。 */
    public static Intake accept(String mime, long declaredSize) {
        return accept(mime, declaredSize, true);
    }

    /**
     * 判定顺序有意为之：
     * <ol>
     *   <li>类型与流都缺 → 先说"没有文件"，因为 {@code null} mime 往往就是纯文本分享那一条；</li>
     *   <li>类型不在白名单 → 说"不支持"，比"没有文件"更准确；</li>
     *   <li>类型对但没有流 → 说"没有文件"；</li>
     *   <li>最后才看体积。</li>
     * </ol>
     */
    public static Intake accept(String mime, long declaredSize, boolean hasStream) {
        String type = base(mime);
        if (type.isEmpty()) return hasStream ? Intake.UNSUPPORTED_MIME : Intake.NO_STREAM;
        if (!whitelisted(type)) return Intake.UNSUPPORTED_MIME;
        if (!hasStream) return Intake.NO_STREAM;
        return tooBig(declaredSize) ? Intake.TOO_LARGE : Intake.OK;
    }

    /** 与清单 intent-filter 一字对一字：{@code text/plain}、{@code image/*}、{@code application/pdf}。 */
    public static boolean supported(String mime) {
        String type = base(mime);
        return !type.isEmpty() && whitelisted(type);
    }

    private static boolean whitelisted(String type) {
        return "text/plain".equals(type)
                || "application/pdf".equals(type)
                || type.startsWith("image/");
    }

    /** 声明体积是否超上限；{@link #UNKNOWN_SIZE} 不算超（真闸门在 ShareInbox 按实际字节把）。 */
    public static boolean tooBig(long declaredSize) {
        if (declaredSize == UNKNOWN_SIZE) return false;
        return declaredSize < 0 || declaredSize > ShareInbox.MAX_BYTES;
    }

    /** 壳生成的分享 id：恒过 {@link IDs} 白名单，所以永远拼不进路径。 */
    public static String newId() {
        StringBuilder sb = new StringBuilder(ID_LENGTH);
        for (int i = 0; i < ID_LENGTH; i++) {
            sb.append(ALPHABET[RANDOM.nextInt(ALPHABET.length)]);
        }
        return sb.toString();
    }

    /**
     * 展示用的文件名：Provider 给了就叫什么，没给就用时间戳兜一个。
     *
     * <p>这里【不】做清洗——{@link ShareInbox} 落账前会砍掉路径分隔符与控制字符并限长，
     * 那一份是唯一的清洗出口；两处各洗一遍只会出现两套"合法名字"。
     */
    public static String displayName(String providerName, String mime, long nowMillis) {
        String name = providerName == null ? "" : providerName.trim();
        if (!name.isEmpty()) return name;
        return "shared-" + nowMillis + "." + extension(mime);
    }

    /** 只认这几个常见扩展名，其余一律 {@code bin}：名字要显示在附件条上，别把外部串拼进去。 */
    public static String extension(String mime) {
        String type = base(mime);
        if ("text/plain".equals(type)) return "txt";
        if ("application/pdf".equals(type)) return "pdf";
        if (!type.startsWith("image/")) return "bin";
        String sub = type.substring("image/".length());
        if ("jpeg".equals(sub)) return "jpg";
        if ("png".equals(sub) || "jpg".equals(sub) || "gif".equals(sub) || "webp".equals(sub)
                || "bmp".equals(sub) || "heic".equals(sub) || "heif".equals(sub)) return sub;
        return "bin";
    }

    /** 去掉参数与大小写：分享方常发 {@code "text/plain; charset=utf-8"}，清单匹配时也是这么折的。 */
    private static String base(String mime) {
        if (mime == null) return "";
        String s = mime.trim().toLowerCase(Locale.US);
        int semi = s.indexOf(';');
        if (semi >= 0) s = s.substring(0, semi);
        return s.trim();
    }
}
