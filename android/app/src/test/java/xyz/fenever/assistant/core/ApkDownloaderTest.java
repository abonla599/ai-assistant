package xyz.fenever.assistant.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;

import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

/**
 * ApkDownloader 的收字节路径（T1.6）。网络接缝换成内存字节后，
 * 2026-09-23 残包事故的每一种进场方式都能在 JVM 台架上重演一遍：
 * 半截、超量、被换、空、跟跳——每一种都必须【没有安装页可弹】地结束，
 * 也就是：target 不存在，temp 不留残渣，错误是一句人能看懂的话。
 */
public class ApkDownloaderTest {

    @Rule public TemporaryFolder folder = new TemporaryFolder();

    private static final byte[] PAYLOAD = new byte[4096];
    static {
        for (int i = 0; i < PAYLOAD.length; i++) PAYLOAD[i] = (byte) (i * 31 + 7);
    }
    private static final String PAYLOAD_SHA = hex(sha256(PAYLOAD));

    private File temp, target;
    private void files() throws IOException {
        temp = new File(folder.getRoot(), "ai-assistant-0.16.apk.part");
        target = new File(folder.getRoot(), "ai-assistant-0.16.apk");
    }

    // ---------- 成功路径 ----------

    @Test
    public void aVerifiedDownloadLandsOnTargetAndNothingElse() throws Exception {
        files();
        ApkDownloader.Result r = ApkDownloader.download("https://x/y", temp, target,
                16L * 1024 * 1024, PAYLOAD_SHA, connector(200, PAYLOAD.length, PAYLOAD, null), null);
        assertTrue(r.error, r.ok());
        assertEquals(PAYLOAD.length, r.bytesWritten);
        assertTrue(target.isFile());
        assertEquals(PAYLOAD.length, target.length());
        assertFalse("归位后 .part 不该还挂着", temp.exists());
    }

    @Test
    public void progressAdvancesMonotonicallyAndEndsAtHundred() throws Exception {
        files();
        final List<Integer> seen = new ArrayList<>();
        ApkDownloader.Result r = ApkDownloader.download("https://x/y", temp, target,
                16L * 1024 * 1024, PAYLOAD_SHA,
                connector(200, PAYLOAD.length, PAYLOAD, null),
                new ApkDownloader.Progress() {
                    @Override public void onProgress(int percent, long done, long total) {
                        seen.add(percent);
                    }
                });
        assertTrue(r.error, r.ok());
        assertFalse(seen.isEmpty());
        assertEquals(Integer.valueOf(100), seen.get(seen.size() - 1));
        int prev = -1;
        for (int percent : seen) {
            assertTrue("进度倒退了：" + seen, percent >= prev);
            prev = percent;
        }
    }

    // ---------- 还没出门就被拦下的 ----------

    @Test
    public void noDigestMeansNoBytesRequestedAtAll() throws Exception {
        files();
        final boolean[] opened = new boolean[1];
        ApkDownloader.Connector c = new ApkDownloader.Connector() {
            @Override public ApkDownloader.Opened open(String url) {
                opened[0] = true;
                throw new AssertionError("没有对账对象就不该碰网络");
            }
        };
        ApkDownloader.Result r = ApkDownloader.download("https://x/y", temp, target,
                16L * 1024 * 1024, null, c, null);
        assertNotNull(r.error);
        assertFalse("没校验值却出了网", opened[0]);
        assertFalse(temp.exists());
        assertFalse(target.exists());
    }

    @Test
    public void aRedirectLandingOnHtmlStopsBeforeAnyBytes() throws Exception {
        files();
        byte[] html = "<html>发布页</html>".getBytes("UTF-8");
        ApkDownloader.Result r = ApkDownloader.download("https://x/y", temp, target,
                16L * 1024 * 1024, PAYLOAD_SHA, connector(302, html.length, html, null), null);
        assertEquals("服务回 HTTP 302", r.error);
        assertFalse(temp.exists());
    }

    @Test
    public void anAbsurdDeclaredSizeNeverOpensTheBody() throws Exception {
        files();
        ApkDownloader.Result r = ApkDownloader.download("https://x/y", temp, target,
                16L * 1024 * 1024, PAYLOAD_SHA,
                connector(200, 64L * 1024 * 1024, PAYLOAD, null), null);
        assertEquals("声明的包体积异常", r.error);
        assertFalse(temp.exists());
    }

    // ---------- 事故进场方式：每一种都要清理，每一种都不许归位 ----------

    @Test
    public void aTruncatedTransferIsDeletedNotInstalled() throws Exception {
        files();
        byte[] half = new byte[PAYLOAD.length / 2];
        System.arraycopy(PAYLOAD, 0, half, 0, half.length);
        // 声明的是整包大小，回来的只有一半——正是那次残包事故的形状
        ApkDownloader.Result r = ApkDownloader.download("https://x/y", temp, target,
                16L * 1024 * 1024, PAYLOAD_SHA, connector(200, PAYLOAD.length, half, null), null);
        assertEquals("下载中断：收下的字节比声明的少", r.error);
        assertFalse("半截文件不许留在盘上", temp.exists());
        assertFalse(target.exists());
    }

    @Test
    public void bytesOverTheCapAreAbortedAndCleaned() throws Exception {
        files();
        byte[] fat = new byte[200_000];
        ApkDownloader.Result r = ApkDownloader.download("https://x/y", temp, target,
                100_000L, hex(sha256(fat)), connector(200, -1L, fat, null), null);
        assertEquals("下载超出体积上限", r.error);
        assertFalse("超量下载的残字节不许留在盘上", temp.exists());
        assertFalse(target.exists());
    }

    @Test
    public void aSwappedPackageFailsDigestAndLeavesNothing() throws Exception {
        files();
        byte[] evil = "不是发布的那一份".getBytes("UTF-8");
        ApkDownloader.Result r = ApkDownloader.download("https://x/y", temp, target,
                16L * 1024 * 1024, PAYLOAD_SHA, connector(200, evil.length, evil, null), null);
        assertEquals("校验值不一致，这个包不是发布的那一份", r.error);
        assertFalse("对不上账的包必须就地删除", temp.exists());
        assertFalse(target.exists());
    }

    @Test
    public void anEmptyBodyIsRefusedEvenIfItsDigestWouldMatch() throws Exception {
        files();
        ApkDownloader.Result r = ApkDownloader.download("https://x/y", temp, target,
                16L * 1024 * 1024, hex(sha256(new byte[0])),
                connector(200, -1L, new byte[0], null), null);
        assertEquals("下载回来是个空文件", r.error);
        assertFalse(temp.exists());
    }

    @Test
    public void aTransportBlowupReportsAndCleansUp() throws Exception {
        files();
        ApkDownloader.Result r = ApkDownloader.download("https://x/y", temp, target,
                16L * 1024 * 1024, PAYLOAD_SHA,
                connector(200, PAYLOAD.length, PAYLOAD, new IOException("模拟断流")), null);
        assertTrue(r.error, r.error.startsWith("下载失败：模拟断流"));
        assertFalse(temp.exists());
        assertFalse(target.exists());
    }

    // ---------- 夹具 ----------

    /** body 读到 throwAt 指定的字节数后（若无）正常结束；声明体积与实发体积可以故意不一致。 */
    private static ApkDownloader.Connector connector(final int status, final long declared,
                                                     final byte[] body, final IOException throwAt) {
        return new ApkDownloader.Connector() {
            @Override public ApkDownloader.Opened open(String url) {
                return new ApkDownloader.Opened() {
                    @Override public int status() { return status; }
                    @Override public long contentLength() { return declared; }
                    @Override public InputStream body() {
                        if (throwAt != null) {
                            return new InputStream() {
                                private int left = body.length / 2;
                                @Override public int read(byte[] b, int off, int len)
                                        throws IOException {
                                    if (left <= 0) throw throwAt;
                                    int n = Math.min(len, left);
                                    System.arraycopy(body, body.length / 2 - left, b, off, n);
                                    left -= n;
                                    return n;
                                }
                                @Override public int read() throws IOException {
                                    return read(new byte[1], 0, 1) < 0 ? -1 : body[body.length / 2] & 0xFF;
                                }
                                @Override public void close() { /* 台架不需要 */ }
                            };
                        }
                        return new ByteArrayInputStream(body);
                    }
                    @Override public void closeQuietly() { /* 台架不需要 */ }
                };
            }
        };
    }

    private static byte[] sha256(byte[] bytes) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(bytes);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private static String hex(byte[] digest) {
        StringBuilder sb = new StringBuilder();
        for (byte b : digest) sb.append(Character.forDigit((b >> 4) & 0xF, 16))
                .append(Character.forDigit(b & 0xF, 16));
        return sb.toString();
    }
}
