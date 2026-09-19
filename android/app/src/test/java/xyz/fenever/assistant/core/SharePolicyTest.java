package xyz.fenever.assistant.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
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
}
