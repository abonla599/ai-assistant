package xyz.fenever.assistant.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.HashSet;
import java.util.Set;
import org.junit.Test;

/**
 * 分享入口的判据（spec §4）。这三条是 Task 7 里唯一不碰 android.* 的部分，
 * 所以单独钉死：mime 白名单、10MB 闸门、id 与文件名。
 * ShareActivity 那边只剩「读流 + 交给 ShareInbox」，没有可测的逻辑。
 */
public class SharePolicyTest {

    private static SharePolicy.Intake decide(String mime, long size) {
        return SharePolicy.accept(mime, size);
    }

    // ---------------------------------------------------------------- 白名单

    @Test public void acceptsExactlyTheThreeFamiliesTheManifestAdvertises() {
        assertEquals(SharePolicy.Intake.OK, decide("text/plain", 10));
        assertEquals(SharePolicy.Intake.OK, decide("image/png", 10));
        assertEquals(SharePolicy.Intake.OK, decide("image/jpeg", 10));
        assertEquals(SharePolicy.Intake.OK, decide("application/pdf", 10));
    }

    @Test public void toleratesCaseAndTrailingParameters() {
        // 分享方给的是 "text/plain; charset=utf-8" 这种带参数的写法，大小写也不统一
        assertEquals(SharePolicy.Intake.OK, decide("TEXT/PLAIN; charset=utf-8", 10));
        assertEquals(SharePolicy.Intake.OK, decide("Image/GIF", 10));
        assertEquals(SharePolicy.Intake.OK, decide("application/pdf;charset=binary", 10));
    }

    @Test public void rejectsEverythingTheShareSheetMightOtherwiseHandUs() {
        // 通配类型、视频、通讯录、联系人 vCard、Word：后端 uploads.py 都不认，收进来只是白占 cache
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, decide("*/*", 10));
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, decide("application/octet-stream", 10));
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, decide("video/mp4", 10));
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, decide("text/vcard", 10));
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, decide("text/html", 10));
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, decide("application/msword", 10));
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, decide(null, 10));
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, decide("", 10));
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, decide("   ", 10));
        // text/uri-list 是 SEND_MULTIPLE 与某些 App 的「链接分享」，本入口不做
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, decide("text/uri-list", 10));
    }

    // ---------------------------------------------------------------- 体积

    @Test public void rejectsOverTenMegabytesAndKeepsTheBoundary() {
        long max = ShareInbox.MAX_BYTES;
        assertEquals(10L * 1024 * 1024, max);                 // 与后端 uploads.py 同数，不许就地改
        assertEquals(SharePolicy.Intake.TOO_LARGE, decide("image/png", max + 1));
        assertEquals(SharePolicy.Intake.OK, decide("image/png", max));
        assertEquals(SharePolicy.Intake.OK, decide("image/png", 1));
    }

    @Test public void unknownDeclaredSizeIsNotAReasonToRefuse() {
        // 个别 Provider 查不出 SIZE（回 -1）。真正的闸门在 ShareInbox.put 里按实际字节数把，
        // 所以这里不能把「不知道多大」当成「太大」——那样大部分相册分享会直接进不来。
        assertEquals(SharePolicy.Intake.OK, decide("image/png", -1));
        assertEquals(SharePolicy.Intake.OK, decide("image/png", 0));
        // 但明显是垃圾的负数（不是 -1 这一约定值）也不放行
        assertEquals(SharePolicy.Intake.TOO_LARGE, decide("image/png", -2));
    }

    @Test public void missingStreamIsReportedSeparatelyFromTheWhitelist() {
        // 只带 EXTRA_TEXT 的纯文本分享会走到这里：拒绝原因必须能区分，
        // 否则提示文案只能说「不支持」，而实际问题是「这条分享里没有文件」。
        assertEquals(SharePolicy.Intake.NO_STREAM,
                SharePolicy.accept("text/plain", 10, false));
        assertEquals(SharePolicy.Intake.NO_STREAM,
                SharePolicy.accept(null, 10, false));
        // 顺序有意：先判 mime 白名单再判有没有流，因为「不支持的类型」是更准确的那句
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME,
                SharePolicy.accept("video/mp4", 10, false));
    }

    // ---------------------------------------------------------------- id

    @Test public void generatedIdsPassTheWhitelistAndDoNotRepeat() {
        Set<String> seen = new HashSet<>();
        for (int i = 0; i < 500; i++) {
            String id = SharePolicy.newId();
            assertEquals(16, id.length());
            assertTrue("id 没过 IDs 白名单: " + id, IDs.valid(id));
            assertTrue("随机 id 撞车了: " + id, seen.add(id));
        }
    }

    @Test public void idsCarryNoCharactersThatNeedEscaping() {
        // 桥那侧只把 id 塞进 {"type":"share","id":"..."} 的固定句式，
        // 所以引号、反斜杠、尖括号、空白出现在 id 里就是注入面。
        for (int i = 0; i < 200; i++) {
            String id = SharePolicy.newId();
            for (char c : new char[]{'"', '\\', '<', '>', ' ', '\n', '\'', ';', '&'}) {
                assertTrue("id 里出现了 " + c, id.indexOf(c) < 0);
            }
        }
    }

    // ---------------------------------------------------------------- 文件名

    @Test public void usesTheProvidersNameWhenThereIsOne() {
        assertEquals("合同.pdf", SharePolicy.displayName("合同.pdf", "application/pdf", 1L));
        assertEquals("a.png", SharePolicy.displayName("a.png", "image/png", 1L));
    }

    @Test public void fallsBackToATimestampNameWithAnExtensionWhenProviderGivesNothing() {
        String name = SharePolicy.displayName(null, "image/jpeg", 1_700_000_000_000L);
        assertEquals("shared-1700000000000.jpg", name);
        assertEquals("shared-1700000000000.txt",
                SharePolicy.displayName("   ", "text/plain", 1_700_000_000_000L));
        assertEquals("shared-1700000000000.pdf",
                SharePolicy.displayName("", "application/pdf", 1_700_000_000_000L));
        // 认不出的子类型不要拼出怪扩展名：Provider 给什么串都可能落在这一位上
        assertEquals("shared-1700000000000.bin",
                SharePolicy.displayName("", "image/x-unknown-format", 1_700_000_000_000L));
    }

    @Test public void extensionNeverCarriesAPathSeparator() {
        // 名字不参与拼路径（路径只由 id 决定，见 ShareInbox），但既然要显示，
        // 就别让 Provider 给的怪 subtype 把分隔符带进来。
        String name = SharePolicy.displayName(null, "image/../etc", 7L);
        assertTrue(name.indexOf('/') < 0);
        assertTrue(name.indexOf('\\') < 0);
        assertNotEquals("shared-7.", name.substring(name.length() - 1));
    }

    /* ---------------------------------------------------------------- EXTRA_TEXT 那一路
     * 只给 EXTRA_TEXT、不给 EXTRA_STREAM 才是"分享一段文字"的常态，所以判据要认这条；
     * 而 1MB 这个数字跟的是 backend/app/core/uploads.py:26，不是图片那 10MB。 */

    @Test public void plainTextWithoutAStreamIsAValidShare() {
        assertEquals(SharePolicy.Intake.OK, SharePolicy.acceptText("text/plain", "一段笔记"));
        assertEquals(SharePolicy.Intake.OK, SharePolicy.acceptText("TEXT/PLAIN; charset=utf-8", "x"));
        // 有的 App 连类型都不填（mime 为 null 或空串），那时 EXTRA_TEXT 就是全部内容
        assertEquals(SharePolicy.Intake.OK, SharePolicy.acceptText(null, "x"));
        assertEquals(SharePolicy.Intake.OK, SharePolicy.acceptText("", "x"));
        assertEquals(SharePolicy.Intake.OK, SharePolicy.acceptText("   ", "x"));
    }

    @Test public void textSharesStillHonourTheMimeWhitelist() {
        // 类型说是图片却没给流：那条分享本身是坏的，不要拿文字去顶一个图片附件
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, SharePolicy.acceptText("image/png", "x"));
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, SharePolicy.acceptText("application/pdf", "x"));
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, SharePolicy.acceptText("text/html", "x"));
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, SharePolicy.acceptText("text/uri-list", "x"));
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, SharePolicy.acceptText("*/*", "x"));
    }

    @Test public void blankOrMissingTextIsStillNoContent() {
        assertEquals(SharePolicy.Intake.NO_STREAM, SharePolicy.acceptText("text/plain", null));
        assertEquals(SharePolicy.Intake.NO_STREAM, SharePolicy.acceptText("text/plain", ""));
        assertEquals(SharePolicy.Intake.NO_STREAM, SharePolicy.acceptText("text/plain", "  \n\t "));
        // 类型不认识排在内容之前：给 video/mp4 配一段文字，问题出在类型上
        assertEquals(SharePolicy.Intake.UNSUPPORTED_MIME, SharePolicy.acceptText("video/mp4", null));
    }

    @Test public void textIsCappedAtOneMegabyteAndCountedInUtf8Bytes() {
        assertEquals(1024L * 1024L, ShareInbox.MAX_TEXT_BYTES);
        // 与后端一致：按 UTF-8 字节数算，不按 char 数
        assertEquals(12L, SharePolicy.utf8Bytes("四个汉字"));
        assertEquals(1L, SharePolicy.utf8Bytes("x"));
        assertEquals(0L, SharePolicy.utf8Bytes(""));
        assertEquals(0L, SharePolicy.utf8Bytes(null));
        assertEquals(SharePolicy.Intake.OK,
                SharePolicy.acceptText("text/plain", textOfBytes((int) ShareInbox.MAX_TEXT_BYTES)));
        assertEquals(SharePolicy.Intake.TEXT_TOO_LARGE,
                SharePolicy.acceptText("text/plain", textOfBytes((int) ShareInbox.MAX_TEXT_BYTES + 1)));
    }

    private static String textOfBytes(int bytes) {
        StringBuilder sb = new StringBuilder(bytes);
        for (int i = 0; i < bytes; i++) sb.append('x');   // 一个 ASCII 一个字节
        return sb.toString();
    }

    // ---------------------------------------------------------------- 交给网页的固定枚举

    /**
     * Theme.NoDisplay 的 Toast 在 Android 12+ 可能整个不显示（无窗口的进程算后台），
     * 所以拒绝原因要经桥交给网页画——而事件里只能出现这几个固定值（spec §2 铁律①）。
     */
    @Test public void everyRefusalMapsToOneFixedBridgeCode() {
        assertEquals("too_large", SharePolicy.code(SharePolicy.Intake.TOO_LARGE));
        assertEquals("text_too_large", SharePolicy.code(SharePolicy.Intake.TEXT_TOO_LARGE));
        assertEquals("unsupported_mime", SharePolicy.code(SharePolicy.Intake.UNSUPPORTED_MIME));
        assertEquals("no_stream", SharePolicy.code(SharePolicy.Intake.NO_STREAM));
        assertEquals("read_failed", SharePolicy.code(SharePolicy.Intake.READ_FAILED));
        // 收下了就不是拒绝：那条链路走的是 pending_share，不该同时报"被拒"
        assertNull(SharePolicy.code(SharePolicy.Intake.OK));
        assertNull(SharePolicy.code(null));
    }

    @Test public void theCodeWhitelistIsExactlyTheRefusalSet() {
        for (SharePolicy.Intake reason : SharePolicy.Intake.values()) {
            String code = SharePolicy.code(reason);
            if (reason == SharePolicy.Intake.OK) {
                assertFalse("OK 不是拒绝原因", SharePolicy.isCode(code));
                continue;
            }
            assertTrue("码没过 IDs 白名单: " + code, SharePolicy.isCode(code));
            assertTrue("码要能安全拼进事件与路径: " + code, IDs.valid(code));
        }
        assertFalse(SharePolicy.isCode(null));
        assertFalse(SharePolicy.isCode(""));
        assertFalse(SharePolicy.isCode("ok"));
        // 外部可控的东西一律不算码
        assertFalse(SharePolicy.isCode("'));alert(1;//xxxx"));
        assertFalse(SharePolicy.isCode("text/plain"));
        assertFalse(SharePolicy.isCode("TOO_LARGE"));
    }
}
