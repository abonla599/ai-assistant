package xyz.fenever.assistant.core;

import static org.junit.Assert.*;

import java.io.ByteArrayInputStream;
import java.io.File;
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
}
