package xyz.fenever.assistant.core;

import static org.junit.Assert.*;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.List;
import java.util.Map;
import org.junit.Before;
import org.junit.Test;

public class ShareInboxTest {
    private File dir;

    @Before public void freshDir() throws Exception {
        dir = Files.createTempDirectory("shares").toFile();
    }

    private static ShareInbox.Io memIo() {
        return new ShareInbox.Io() {
            String blob = "";
            public String read() { return blob; }
            public void write(String content) { blob = content; }
        };
    }

    private static ByteArrayInputStream data(String s) {
        // 计划原文是 s.getBytes("UTF-8")，那重载声明了受检的
        // UnsupportedEncodingException，而 data() 没有 throws（三个不加 throws 的
        // 测试也要调它），照抄根本编不过。换成常量形式，语义一字不差。
        return new ByteArrayInputStream(s.getBytes(StandardCharsets.UTF_8));
    }

    @Test public void rejectsIdsWithPathTraversalOrWrongLength() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        assertFalse(inbox.put("../etc/passwd", data("x"), "n", "image/png", 1));
        assertFalse(inbox.put("short", data("x"), "n", "image/png", 1));
        assertFalse(inbox.put("has space12345", data("x"), "n", "image/png", 1));
    }

    @Test public void storesBytesUnderItsOwnPathAndReadsBackChunks() throws Exception {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        assertTrue(inbox.put("s0123456789ab", data("hello world"), "a.png", "image/png", 11));
        assertArrayEquals("hello".getBytes("UTF-8"), inbox.chunk("s0123456789ab", 0, 5));
        assertArrayEquals("world".getBytes("UTF-8"), inbox.chunk("s0123456789ab", 6, 5));
        assertEquals(0, inbox.chunk("s0123456789ab", 99, 5).length);
    }

    @Test public void refusesAnythingOverTenMegabytes() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        assertFalse(inbox.put("s0123456789ac", data("x"), "big.png", "image/png",
                10L * 1024 * 1024 + 1));
    }

    /**
     * Task 7 那条硬约束的下半场：Provider 报不出体积（或报小了）时，闸门要按【实际读到的
     * 字节数】把，而且失败之后不许留下半份文件——ShareActivity 跑在别人相册的分享链路上，
     * 留一份永远没人来读的 10MB 残件等于把用户 cache 占死。
     *
     * <p>这个假流【不】一次性分配 10MB：它顺着"8KB 缓冲流式复制、不整块读进内存"那条要求写，
     * 所以超上限也只能靠读够字节来发现，而不是靠 array.length。
     */
    @Test public void aLyingDeclaredSizeCannotSmugglePastTheCapOrLeaveAHalfWrittenFile() {
        InputStream endless = new InputStream() {
            private long left = 10L * 1024 * 1024 + 1;

            @Override public int read(byte[] buf, int off, int len) {
                if (left <= 0) return -1;
                int n = (int) Math.min(Math.min(len, buf.length - off), left);
                left -= n;
                return n;
            }

            @Override public int read() {
                byte[] one = new byte[1];
                return read(one, 0, 1) < 0 ? -1 : 0;
            }
        };
        ShareInbox inbox = new ShareInbox(dir, memIo());
        inbox.setOwner("alice");
        // -1 = 体积未知：前置那一刀放行，真正拦下它的是写入时按字节数把的那一刀
        assertFalse(inbox.put("s0123456789ad", endless, "big.jpg", "image/jpeg", -1));
        assertEquals(0, dir.listFiles().length);
        assertTrue(inbox.pending().isEmpty());
    }

    @Test public void pendingIsEmptyUntilAnOwnerIsSet() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        inbox.put("s0123456789ab", data("x"), "a.png", "image/png", 1);
        assertTrue(inbox.pending().isEmpty());
        inbox.setOwner("alice");
        assertEquals(1, inbox.pending().size());
        assertEquals("a.png", inbox.pending().get(0).get("name"));
    }

    @Test public void consumeDeletesTheFileAndReportsMisses() throws Exception {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        inbox.setOwner("alice");
        inbox.put("s0123456789ab", data("x"), "a.png", "image/png", 1);
        assertTrue(inbox.consume("s0123456789ab"));
        assertFalse(inbox.consume("s0123456789ab"));
        assertEquals(0, dir.listFiles().length);
    }

    @Test public void sweepDropsEntriesOlderThanThirtyMinutes() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        inbox.setOwner("alice");
        long t0 = 1_700_000_000_000L;
        inbox.put("s0123456789ab", data("x"), "a.png", "image/png", 1, t0);
        inbox.sweepExpired(t0 + 31L * 60_000L);
        assertTrue(inbox.pending().isEmpty());
    }

    @Test public void listingSurvivesReopen() {
        ShareInbox.Io io = memIo();
        ShareInbox first = new ShareInbox(dir, io);
        first.setOwner("alice");
        first.put("s0123456789ab", data("x"), "a.png", "image/png", 1);
        ShareInbox reopened = new ShareInbox(dir, io);
        reopened.setOwner("alice");
        List<Map<String, Object>> rows = reopened.pending();
        assertEquals(1, rows.size());
        assertEquals(1L, rows.get(0).get("size"));
    }

    /** spec §6 声称覆盖"文件名清洗"，计划里却一条测试都没有。补上。
     *  注意这里走的是公开表面 put/pending，不去碰私有的 safeName——清洗只在
     *  作为展示名交给网页时才有意义，磁盘上的文件名永远是那个校验过的 id。
     *  按 id 取，不对 pending() 的顺序做假设：三次 put 落在同一毫秒，
     *  addedAt 升序对它们没有约束力，位置断言会变成碰巧绿。 */
    @Test public void displayNameLosesPathsControlCharsAndExtraLength() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        inbox.setOwner("alice");

        inbox.put("s0123456789ab", data("x"), "..\\..\\windows\\system32\\cmd.exe", "image/png", 1);
        assertEquals("cmd.exe", nameOf(inbox, "s0123456789ab"));

        inbox.put("s0123456789ac", data("y"), "a\tb" + '\0' + "c.png", "image/png", 1);
        assertEquals("a b c.png", nameOf(inbox, "s0123456789ac"));

        StringBuilder longName = new StringBuilder();
        for (int i = 0; i < 200; i++) longName.append('x');
        inbox.put("s0123456789ad", data("z"), longName.toString(), "image/png", 1);
        assertEquals(128, nameOf(inbox, "s0123456789ad").length());
    }

    private static String nameOf(ShareInbox inbox, String id) {
        for (Map<String, Object> row : inbox.pending()) {
            if (id.equals(row.get("id"))) return (String) row.get("name");
        }
        return null;
    }

    @Test public void anEmptyOrBlankDisplayNameFallsBackToShared() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        inbox.setOwner("alice");
        inbox.put("s0123456789ab", data("x"), "   ", "image/png", 1);
        assertEquals("shared", inbox.pending().get(0).get("name"));
    }

    /* ---------------------------------------------------------------- 纯文本分享
     * 别的 App 分享文字时通常只给 EXTRA_TEXT、不给 EXTRA_STREAM，所以文本要能落成一个小文件，
     * 走【同一套】id、TTL 与消费规则（而不是另开一条只读文本的旁路）。
     * 体积上限跟的是 backend/app/core/uploads.py:26 的 MAX_TEXT_BYTES = 1MB，不是那 10MB。 */

    @Test public void textBecomesAPlainFileUnderTheSameIdAndCleanupRules() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        inbox.setOwner("alice");
        byte[] utf8 = "第一段笔记".getBytes(StandardCharsets.UTF_8);

        assertTrue(inbox.putText("s0123456789ab", "第一段笔记", "shared-note.txt"));

        assertEquals(1, inbox.pending().size());
        Map<String, Object> row = inbox.pending().get(0);
        assertEquals("shared-note.txt", row.get("name"));
        assertEquals("text/plain", row.get("mime"));
        // 记的是 UTF-8 字节数：后端按字节截断，按 char 数记会让附件条显示的大小与实际不符
        assertEquals((long) utf8.length, row.get("size"));
        assertArrayEquals(utf8, inbox.chunk("s0123456789ab", 0, 100));

        // 与文件件共用同一条消费路径：读完即删，磁盘上不留残件
        assertTrue(inbox.consume("s0123456789ab"));
        assertEquals(0, dir.listFiles().length);
    }

    @Test public void textSharesExpireOnTheSameThirtyMinuteClock() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        inbox.setOwner("alice");
        long t0 = 1_700_000_000_000L;
        assertTrue(inbox.putText("s0123456789ab", "等会被读走的笔记", "n.txt", t0));
        inbox.sweepExpired(t0 + 31L * 60_000L);
        assertTrue(inbox.pending().isEmpty());
        assertEquals(0, dir.listFiles().length);
    }

    /** 未登录时分享的文本与图片同命：先当孤儿，第一个 setOwner 的人认领（spec §4 改判）。 */
    @Test public void orphanTextIsClaimedByTheFirstOwnerJustLikeOrphanFiles() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        assertTrue(inbox.putText("s0123456789ab", "登录前分享的笔记", "n.txt"));
        assertTrue(inbox.pending().isEmpty());
        inbox.setOwner("alice");
        assertEquals(1, inbox.pending().size());
    }

    @Test public void textIdsGoThroughTheSameWhitelistAndEmptyTextIsNoContent() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        inbox.setOwner("alice");
        assertFalse(inbox.putText("../etc/passwd", "x", "n.txt"));
        assertFalse(inbox.putText("short", "x", "n.txt"));
        assertFalse(inbox.putText("s0123456789ab", null, "n.txt"));
        assertFalse(inbox.putText("s0123456789ac", "", "n.txt"));
        assertEquals(0, dir.listFiles().length);
        assertTrue(inbox.pending().isEmpty());
    }

    /** 1MB 这条线：正好收、超一个字节拒，并且拒了不许留下半份文件。
     *  汉字按 3 字节计，所以「字数没超、字节超」那种情况也必须拦下。 */
    @Test public void textIsCappedAtTheOnesizeTheBackendAccepts() {
        assertEquals(1024L * 1024L, ShareInbox.MAX_TEXT_BYTES);
        ShareInbox inbox = new ShareInbox(dir, memIo());
        inbox.setOwner("alice");

        assertTrue(inbox.putText("s0123456789ab", repeat('x', (int) ShareInbox.MAX_TEXT_BYTES), "a.txt"));
        assertFalse(inbox.putText("s0123456789ac", repeat('x', (int) ShareInbox.MAX_TEXT_BYTES + 1), "b.txt"));
        // 40 万汉字 = 1.2MB 字节，char 数看着还远不到 1MB
        assertFalse(inbox.putText("s0123456789ad", repeat('笔', 400_000), "c.txt"));

        assertEquals(1, inbox.pending().size());
        assertEquals("a.txt", inbox.pending().get(0).get("name"));
        assertEquals(1, dir.listFiles().length);
    }

    private static String repeat(char c, int times) {
        StringBuilder sb = new StringBuilder(times);
        for (int i = 0; i < times; i++) sb.append(c);
        return sb.toString();
    }
}
