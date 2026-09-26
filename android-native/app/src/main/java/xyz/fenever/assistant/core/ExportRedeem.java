package xyz.fenever.assistant.core;

import java.nio.charset.StandardCharsets;

/**
 * 导出异常路径的判定与文案（v0.23 T2.5，PRD R2 边缘与 AC-2/3）。
 *
 * <p>票据 5 分钟有效、兑换一次即作废。原生把兑换交给 DownloadManager 之后，
 * "过期"表现为一次 HTTP 失败、响应体是服务端那行 detail 的小 JSON——但
 * DownloadManager 不把 HTTP 状态码透出来，能拿到的只有失败记录的响应字节。
 * 于是"识别过期"变成一串字符串运算：判据、文案、重试预算的形状都收在这里，
 * 纯 JVM、可台架，Android 侧只负责把字节递过来。
 *
 * <p>{@link #TICKET_INVALID_DETAIL} 必须与服务端
 * backend/app/main.py 的 EXPORT_TICKET_INVALID_DETAIL 逐字同值——判据认的就是
 * 这行字。后端钉死测试（test_release_probe）两头各数各的：漂了先红，而不是
 * 过期被误判成"其他网络失败"、白占重试预算还不重签。
 */
public final class ExportRedeem {

    private ExportRedeem() {
    }

    /** 服务端对"票据不存在/已过期/已兑换"的统一回答（三种情况刻意不可区分）。 */
    public static final String TICKET_INVALID_DETAIL = "导出链接无效或已过期";

    private static final byte[] DETAIL_UTF8 =
            TICKET_INVALID_DETAIL.getBytes(StandardCharsets.UTF_8);

    /**
     * 失败下载的响应体是否就是"票据失效"那一问。
     *
     * <p>只做子串查找，不做 JSON 解析：错误体可能只读回前几 KB、可能带 BOM、
     * 也可能哪天中间多一层代理。判据越钝越稳——认这行字在不在，别认它长在哪。
     */
    public static boolean looksExpired(byte[] body) {
        if (body == null || body.length < DETAIL_UTF8.length) {
            return false;
        }
        outer:
        for (int i = 0; i + DETAIL_UTF8.length <= body.length; i++) {
            for (int j = 0; j < DETAIL_UTF8.length; j++) {
                if (body[i + j] != DETAIL_UTF8[j]) {
                    continue outer;
                }
            }
            return true;
        }
        return false;
    }

    /** 判据的 UTF-8 字节本身（台架用来自证镜像的是那行字，不是手抄的第二份）。 */
    public static byte[] ticketDetailUtf8() {
        return DETAIL_UTF8.clone();
    }

    /** 终失败的文案。expired 与否各一条，都是能直接读给人听的一句话（AC-2）。 */
    public static String failureNotice(boolean expired) {
        return expired
                ? "导出失败：" + TICKET_INVALID_DETAIL + "（已自动重签一次，仍未换成文件）"
                : "导出失败：下载未完成，请检查网络后重试";
    }

    /** 轮询用尽仍没有结局的文案：DownloadManager 可能还在等网络，别谎报成功。 */
    public static String timeoutNotice() {
        return "导出未完成：可能仍在等待网络或链接已失效，可在设置里重试";
    }

    /**
     * 入队后的轮询节奏（毫秒）。首查 8 秒是为了尽快抓住"秒失败"的过期票据，
     * 后面拉开间隔等慢网络；总时长用尽仍未结局 → timeoutNotice，不死等。
     */
    public static int[] watchDelaysMs() {
        return new int[]{8000, 30000, 60000, 120000, 240000};
    }
}
