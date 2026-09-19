package xyz.fenever.assistant.core;

import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Locale;

/**
 * 系统分享入口（spec §4）的判据：哪些件收、多大算太大、给它起什么名字与 id、
 * 被拒时以哪个固定码回给网页。
 *
 * <p>收在 core 里是因为这些就是 Task 7 全部的可判定逻辑，而 {@code ShareActivity}
 * 那一侧只剩「查列 → 拷字节 → 交给 {@link ShareInbox}」，在 JVM 里测不了。
 *
 * <p>两条入口：带 {@code EXTRA_STREAM} 的走 {@link #accept}（10MB 那道闸），
 * 只带 {@code EXTRA_TEXT} 的走 {@link #acceptText}（1MB，后端只收这么大的文本）。
 *
 * <p>这里【不】做真正的体积闸门——那在 {@link ShareInbox#put} 里按【实际读到的字节数】把，
 * 因为 Provider 报的 {@code OpenableColumns.SIZE} 可以撒谎或缺失。本类只是拿声明值提前拒绝，
 * 好让入口能给出明确提示（spec §9 第 6 条：>10MB 要"有明确拒绝提示，不是静默没反应"）。
 */
public final class SharePolicy {

    /** 一次分享的处置结果。每一种各配一句提示与一个固定码，不共用「失败」这一句。 */
    public enum Intake {
        OK,
        /** 既没有 {@code EXTRA_STREAM} 也没有可用的 {@code EXTRA_TEXT}：这条分享里没有内容。 */
        NO_STREAM,
        /** 白名单之外的类型：清单里已经只声明了三种，走到这里说明是绕进来的。 */
        UNSUPPORTED_MIME,
        /** 声明体积超过 {@link ShareInbox#MAX_BYTES}（10MB）。 */
        TOO_LARGE,
        /** 纯文本分享超过 {@link ShareInbox#MAX_TEXT_BYTES}（1MB，后端 uploads.py:26 同数）。 */
        TEXT_TOO_LARGE,
        /** 判据都过了但字节没落下来：Provider 挂了、读断在中间、实际体积超了、目录建不出来。 */
        READ_FAILED
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
     * 带流那一路的判定顺序有意为之：
     * <ol>
     *   <li>类型与流都缺 → 先说"没有内容"（{@code null} mime 又没有流时，纯文本那条由
     *       {@link #acceptText} 另行接住，走到这里说明文本也是空的）；</li>
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

    /**
     * 只带 {@code EXTRA_TEXT} 的那一路：别的 App 分享一段文字时几乎从不给 {@code EXTRA_STREAM}
     * （v0.13 之前这里被判成「没有文件」直接拒收，于是 {@code text/plain} 那条声明基本走不通）。
     *
     * <ol>
     *   <li>类型只认 {@code text/plain}，外加「压根没填类型」——确实有 App 发 SEND 时 type 是 null
     *       而内容全在 EXTRA_TEXT 里；说是图片/PDF 却没给流的，那是那条分享本身坏了，
     *       不能拿一段文字去顶一个图片附件（体积、mime、扩展名全对不上）；</li>
     *   <li>空白文本按「没有内容」处理，别落一个 0 字节的件进队列；</li>
     *   <li>闸门是 {@link ShareInbox#MAX_TEXT_BYTES}（1MB），按 UTF-8 字节数算。</li>
     * </ol>
     */
    public static Intake acceptText(String mime, String text) {
        String type = base(mime);
        if (!type.isEmpty() && !"text/plain".equals(type)) return Intake.UNSUPPORTED_MIME;
        if (text == null || text.trim().isEmpty()) return Intake.NO_STREAM;
        return utf8Bytes(text) > ShareInbox.MAX_TEXT_BYTES ? Intake.TEXT_TOO_LARGE : Intake.OK;
    }

    /** UTF-8 字节数：后端按字节截断与拒收，所以这里也不能按 char 数估（汉字 3 字节）。 */
    public static long utf8Bytes(String text) {
        if (text == null || text.isEmpty()) return 0L;
        return text.getBytes(StandardCharsets.UTF_8).length;
    }

    /**
     * 这个字符串是不是一个可以穿过桥的拒绝码。判据【就是 {@link Intake} 本身】：
     * 五个拒绝值的小写名，别的一种都不算。
     *
     * <p>这么设计是为了堵住「拒绝原因顺手把文件名带上」那次走偏——分享来的名字是外部可控
     * 字符串，事件会被拼进 {@code evaluateJavascript} 的单引号里（spec §2 铁律①）。
     * 所以过这道闸的只有固定枚举，且每个都还过 {@link IDs} 的字符白名单（见单测）。
     */
    public static boolean isCode(String code) {
        if (code == null) return false;
        for (Intake reason : Intake.values()) {
            if (reason != Intake.OK && reason.name().toLowerCase(Locale.US).equals(code)) return true;
        }
        return false;
    }

    /** {@link #isCode} 的反方向：处置结果 → 交给网页的码；{@code OK} 没有码（那条链路走 pending_share）。 */
    public static String code(Intake reason) {
        if (reason == null || reason == Intake.OK) return null;
        return reason.name().toLowerCase(Locale.US);
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
