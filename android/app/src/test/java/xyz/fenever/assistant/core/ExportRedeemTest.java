package xyz.fenever.assistant.core;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.nio.charset.StandardCharsets;

/**
 * 导出异常判定的台架用例（v0.23 T2.5）。
 *
 * <p>"过期"的原生证据只有一坨失败下载的响应字节——判错的代价是白占重试预算
 * 或者错报网络问题，所以这里把识别的边界条件全部钉死：命中、未命中、被截断、
 * 空体、恰好在边界。常量与服务端的逐字同值由后端钉死测试数，不在这里重复。
 */
public class ExportRedeemTest {

    private static byte[] utf8(String s) {
        return s.getBytes(StandardCharsets.UTF_8);
    }

    @Test
    public void theServerErrorBodyIsRecognised() {
        assertTrue(ExportRedeem.looksExpired(
                utf8("{\"detail\":\"导出链接无效或已过期\"}")));
        // 判据是子串，不要求 JSON 完整或编码格式：截断的、带前缀的都要认
        assertTrue(ExportRedeem.looksExpired(utf8("…无效中间截" + "导出链接无效或已过期")));
        assertTrue(ExportRedeem.looksExpired(utf8(ExportRedeem.TICKET_INVALID_DETAIL)));
    }

    @Test
    public void otherFailuresAreNotMistakenForExpiry() {
        assertFalse(ExportRedeem.looksExpired(utf8("<html>502 Bad Gateway</html>")));
        assertFalse(ExportRedeem.looksExpired(utf8("{\"detail\":\"会话不存在\"}")));
        assertFalse(ExportRedeem.looksExpired(new byte[0]));
        assertFalse(ExportRedeem.looksExpired(null));
        // 差一个字节就被截断：宁可认不出来（走通用失败文案），不许瞎猜过期
        byte[] truncated = utf8("导出链接无效或已过期");
        byte[] cut = new byte[truncated.length - 1];
        System.arraycopy(truncated, 0, cut, 0, cut.length);
        assertFalse(ExportRedeem.looksExpired(cut));
    }

    @Test
    public void theDetailBytesMirrorTheConstantItself() {
        assertArrayEquals(utf8(ExportRedeem.TICKET_INVALID_DETAIL),
                ExportRedeem.ticketDetailUtf8());
    }

    @Test
    public void everyTerminalFailureHasItsOwnReadableSentence() {
        String expired = ExportRedeem.failureNotice(true);
        String other = ExportRedeem.failureNotice(false);
        assertTrue(expired, expired.startsWith("导出失败："));
        assertTrue(other, other.startsWith("导出失败："));
        assertNotEquals(expired, other);
        // 过期那条必须把服务端的原话带出来，用户拿它对得上后台日志
        assertTrue(expired, expired.contains(ExportRedeem.TICKET_INVALID_DETAIL));
        // 超时不许谎报：单独一句，不落进"失败"也不落进"成功"
        String timeout = ExportRedeem.timeoutNotice();
        assertTrue(timeout, timeout.startsWith("导出未完成"));
    }

    @Test
    public void theWatchScheduleIsMonotonicAndBounded() {
        int[] delays = ExportRedeem.watchDelaysMs();
        assertTrue(delays.length >= 4);
        long total = 0;
        for (int i = 0; i < delays.length; i++) {
            assertTrue(delays[i] > 0);
            if (i > 0) {
                assertTrue(delays[i] > delays[i - 1]);
            }
            total += delays[i];
        }
        // 轮询总时长超过票据 300 秒 TTL：慢网络下一定要等到"过期成既成事实"之后再判
        assertTrue("总时长 " + total, total > 300_000);
        // 但也必须有个头：不许无限挂着轮询
        assertEquals(5, delays.length);
    }
}
