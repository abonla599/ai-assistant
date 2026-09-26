package xyz.fenever.assistant.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.Test;

/** ReleasePlan 的判定表。这条链路的终点是"装一个 APK"，所以每一条放行都要有名字。 */
public class ReleasePlanTest {

    private static final String GOOD_URL =
            "https://github.com/abonla599/ai-assistant/releases/download/v0.15/ai-assistant-native-0.15.apk";

    // ---------- 版本比较：字符串比会错的那一类 ----------

    @Test
    public void comparesByNumericSegmentsNotByString() {
        assertTrue(ReleasePlan.compare("0.9", "0.10") < 0);
        assertTrue(ReleasePlan.compare("0.10", "0.9") > 0);
        assertEquals(0, ReleasePlan.compare("0.15", "0.15"));
        assertTrue(ReleasePlan.compare("0.15", "0.15.1") < 0);
        assertTrue(ReleasePlan.compare("0.15.1", "0.16") < 0);
        assertEquals(0, ReleasePlan.compare("0.15", "0.15.0"));
    }

    @Test
    public void normalizesTheLeadingVOnly() {
        assertEquals("0.15", ReleasePlan.normalizeTag("v0.15"));
        assertEquals("0.15", ReleasePlan.normalizeTag("V0.15"));
        assertEquals("0.15", ReleasePlan.normalizeTag("  v0.15  "));
        assertNull(ReleasePlan.normalizeTag("nightly"));
        assertNull(ReleasePlan.normalizeTag("vv0.15"));
        assertNull(ReleasePlan.normalizeTag("0.15-beta"));
        assertNull(ReleasePlan.normalizeTag("0..15"));
        assertNull(ReleasePlan.normalizeTag("0.15."));
        assertNull(ReleasePlan.normalizeTag(null));
    }

    // ---------- 三态 ----------

    @Test
    public void aNewerReleaseWithATrustworthyAssetIsAvailable() {
        ReleasePlan.Decision d = ReleasePlan.decide("0.14", release("v0.15", GOOD_URL,
                "ai-assistant-native-0.15.apk", 103_000L, "修了两个 bug", false, false));
        assertEquals(ReleasePlan.Kind.AVAILABLE, d.kind);
        assertEquals("0.15", d.version);
        assertEquals(GOOD_URL, d.url);
        assertEquals(103_000L, d.sizeBytes);
        assertTrue(d.notes.contains("修了两个 bug"));
        assertNull(d.reason);
    }

    @Test
    public void equalOrOlderLatestMeansUpToDate() {
        assertEquals(ReleasePlan.Kind.UP_TO_DATE,
                ReleasePlan.decide("0.15", release("v0.15", GOOD_URL,
                        "ai-assistant-native-0.15.apk", 1L, null, false, false)).kind);
        // 机器上装着比"最新"更新的版本（测试机 / 手装过预发包）：也不许再劝人下载
        assertEquals(ReleasePlan.Kind.UP_TO_DATE,
                ReleasePlan.decide("0.16", release("v0.15", GOOD_URL,
                        "ai-assistant-native-0.15.apk", 1L, null, false, false)).kind);
    }

    @Test
    public void anUnreadableResponseIsNeverReportedAsUpToDate() {
        // 正对照：下面每一条都必须落在 UNUSABLE，而不是被"读不出来"顺手下拉成 UP_TO_DATE。
        // 限流页 / 代理改写的 HTML / 空体，都是真实会回来的东西。
        String[] junk = {"<html>403</html>", "", "null", "[]", "\"x\"",
                "{\"tag_name\":\"v0.15\"}", "{\"tag_name\":123}", "{\"assets\":{}}"};
        for (String body : junk) {
            ReleasePlan.Decision d = ReleasePlan.decide("0.14", body);
            assertEquals("垃圾输入被当成 " + d.kind + "：" + body, ReleasePlan.Kind.UNUSABLE, d.kind);
            assertNotNull("UNUSABLE 得带一句原因：" + body, d.reason);
        }
    }

    @Test
    public void draftAndPrereleaseAreNotInstalledSilently() {
        assertEquals(ReleasePlan.Kind.UNUSABLE, ReleasePlan.decide("0.14",
                release("v0.15", GOOD_URL, "ai-assistant-native-0.15.apk", 1L, null, true, false)).kind);
        assertEquals(ReleasePlan.Kind.UNUSABLE, ReleasePlan.decide("0.14",
                release("v0.15", GOOD_URL, "ai-assistant-native-0.15.apk", 1L, null, false, true)).kind);
    }

    @Test
    public void aMalformedLocalVersionRefusesInsteadOfCrashing() {
        ReleasePlan.Decision d = ReleasePlan.decide("0.14-debug", release("v0.15", GOOD_URL,
                "ai-assistant-native-0.15.apk", 1L, null, false, false));
        assertEquals(ReleasePlan.Kind.UNUSABLE, d.kind);
        assertTrue(d.reason, d.reason.contains("本机"));
    }

    // ---------- 资产名：只认那一个 ----------

    @Test
    public void onlyTheAssetNamedAfterTheVersionIsAccepted() {
        // 名字里的版本与 tag 对不上：可能是上一次误传没删掉的包
        assertEquals(ReleasePlan.Kind.UNUSABLE, ReleasePlan.decide("0.14",
                release("v0.15", GOOD_URL, "ai-assistant-native-0.14.apk", 1L, null, false, false)).kind);
        assertEquals(ReleasePlan.Kind.UNUSABLE, ReleasePlan.decide("0.14",
                release("v0.15", GOOD_URL, "app.apk", 1L, null, false, false)).kind);
    }

    @Test
    public void picksTheMatchingAssetAmongSeveral() {
        String json = "{"
                + "\"tag_name\":\"v0.15\",\"draft\":false,\"prerelease\":false,"
                + "\"body\":\"说明\","
                + "\"assets\":["
                + "{\"name\":\"mapping.txt\",\"browser_download_url\":\"" + GOOD_URL + "\",\"size\":9}"
                + ",{\"name\":\"ai-assistant-native-0.15.apk\",\"browser_download_url\":\"" + GOOD_URL
                + "\",\"size\":98765}"
                + ",{\"name\":\"other.apk\",\"browser_download_url\":\"" + GOOD_URL + "\",\"size\":1}"
                + "]}";
        ReleasePlan.Decision d = ReleasePlan.decide("0.14", json);
        assertEquals(ReleasePlan.Kind.AVAILABLE, d.kind);
        assertEquals(98765L, d.sizeBytes);
    }

    // ---------- 下载地址：每一项都要过 ----------

    @Test
    public void theGoodDownloadUrlPasses() {
        assertTrue(ReleasePlan.downloadUrlIsTrusted(GOOD_URL, "ai-assistant-native-0.15.apk"));
    }

    @Test
    public void everyDetourInTheUrlIsRefused() {
        String[] bad = {
                "http://github.com/abonla599/ai-assistant/releases/download/v0.15/ai-assistant-native-0.15.apk",
                "https://evil.com/abonla599/ai-assistant/releases/download/v0.15/ai-assistant-native-0.15.apk",
                // 前缀对上但主机是它的子域替身
                "https://github.com.evil.com/abonla599/ai-assistant/releases/download/v0.15/ai-assistant-native-0.15.apk",
                "https://objects.githubusercontent.com/abonla599/ai-assistant/releases/download/v0.15/ai-assistant-native-0.15.apk",
                // userinfo 伪装：URL 解析后真正的 host 是 evil.com
                "https://github.com@evil.com/abonla599/ai-assistant/releases/download/v0.15/ai-assistant-native-0.15.apk",
                "https://github.com:8080/abonla599/ai-assistant/releases/download/v0.15/ai-assistant-native-0.15.apk",
                "https://github.com/other/ai-assistant/releases/download/v0.15/ai-assistant-native-0.15.apk",
                "https://github.com/abonla599/ai-assistant/releases/download/v0.15/evil.apk",
                "https://github.com/abonla599/ai-assistant/releases/latest/ai-assistant-native-0.15.apk",
                "https://github.com/abonla599/ai-assistant/releases/download/v0.15/../evil.apk",
                "not a url", "", null,
        };
        for (String url : bad) {
            assertFalse("这个地址不该放行：" + url,
                    ReleasePlan.downloadUrlIsTrusted(url, "ai-assistant-native-0.15.apk"));
        }
    }

    @Test
    public void anUntrustedUrlMakesTheWholeReleaseUnusable() {
        String json = release("v0.15", "https://evil.com/x/ai-assistant-native-0.15.apk",
                "ai-assistant-native-0.15.apk", 1L, null, false, false);
        ReleasePlan.Decision d = ReleasePlan.decide("0.14", json);
        assertEquals(ReleasePlan.Kind.UNUSABLE, d.kind);
        assertNull(d.url);
    }

    // ---------- 正文截断 ----------

    @Test
    public void longNotesAreCutWithAPointerNotMidSentence() {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < 400; i++) sb.append("很长的一句更新说明。");
        ReleasePlan.Decision d = ReleasePlan.decide("0.14", release("v0.15", GOOD_URL,
                "ai-assistant-native-0.15.apk", 1L, sb.toString(), false, false));
        assertEquals(ReleasePlan.Kind.AVAILABLE, d.kind);
        assertTrue(d.notes.length() < sb.length() / 2);
        assertTrue(d.notes, d.notes.endsWith("完整说明在 Release 页"));
    }

    @Test
    public void missingBodyMeansNoNotesRatherThanTheWordNull() {
        ReleasePlan.Decision d = ReleasePlan.decide("0.14", release("v0.15", GOOD_URL,
                "ai-assistant-native-0.15.apk", 1L, "   ", false, false));
        assertEquals(ReleasePlan.Kind.AVAILABLE, d.kind);
        assertNull(d.notes);
    }

    @Test
    public void theOnlyAssetShapeIsTheVersionNamedApk() {
        // 这一层不再持有"去哪问"的地址（v0.19 起问与取都在自家服务器，
        // "壳源码不出现 GitHub API 地址" 由 backend/tests/test_android_shell.py 数着）；
        // 留在这里的是"只要那一个资产名"的形状。
        assertTrue(ReleasePlan.assetName("0.15").equals("ai-assistant-native-0.15.apk"));
    }

    // ---------- 第二条信任规则：自家字节出口 = APP_URL 同源 + 精确路径（T1.5） ----------

    private static final String APP = "https://ai.fenever.xyz/app/";
    private static final String SELF_URL = "https://ai.fenever.xyz/site/android.apk";

    @Test
    public void theSelfHostedByteExitIsTrustedOnlyWhenTheCallerHandsOverAppUrl() {
        // 放大信任的入口是新调用方显式交出 APP_URL 的那一刻；两参旧形状行为不变。
        assertTrue(ReleasePlan.downloadUrlIsTrusted(SELF_URL, "ai-assistant-native-0.15.apk", APP));
        assertFalse(ReleasePlan.downloadUrlIsTrusted(SELF_URL, "ai-assistant-native-0.15.apk"));
        assertFalse(ReleasePlan.downloadUrlIsTrusted(SELF_URL, "ai-assistant-native-0.15.apk", null));
        assertFalse(ReleasePlan.downloadUrlIsTrusted(SELF_URL, "ai-assistant-native-0.15.apk", ""));
        // 自家来源说不利索（非 https / 带端口 / 带 userinfo），第二条规则整体不启用——宁可窄
        assertFalse(ReleasePlan.downloadUrlIsTrusted(SELF_URL, "x", "http://ai.fenever.xyz/app/"));
        assertFalse(ReleasePlan.downloadUrlIsTrusted(SELF_URL, "x", "https://ai.fenever.xyz:8443/app/"));
        assertFalse(ReleasePlan.downloadUrlIsTrusted(SELF_URL, "x", "https://ops@ai.fenever.xyz/app/"));
        assertFalse(ReleasePlan.downloadUrlIsTrusted(SELF_URL, "x", "not a url"));
    }

    @Test
    public void everyDetourFromTheExactSameOriginPathIsRefused() {
        String[] bad = {
                // 主机不是那一个：子域替身、别家、userinfo 伪装
                "https://ai.fenever.xyz.eil.com/site/android.apk",
                "https://eil.com/site/android.apk",
                "https://ai.fenever.xyz:8734/site/android.apk",
                "https://fenever.xyz/site/android.apk",
                "http://ai.fenever.xyz/site/android.apk",
                // 路径必须【整串相等】：近似、前缀、大小写、尾巴、query、fragment 统统不算
                "https://ai.fenever.xyz/site/other.apk",
                "https://ai.fenever.xyz/Site/android.apk",
                "https://ai.fenever.xyz/site/android.apk/",
                "https://ai.fenever.xyz/site/android.apk?force=1",
                "https://ai.fenever.xyz/site/android.apk#v2",
                "https://ai.fenever.xyz/site/android.apk/../evil.apk",
                "https://ai.fenever.xyz/../site/android.apk",
                "https://ai.feverov.xyz/site/android.apk",   // 只差一个字母
        };
        for (String url : bad) {
            assertFalse("这个地址不该走第二条规则放行：" + url,
                    ReleasePlan.downloadUrlIsTrusted(url, "ai-assistant-native-0.15.apk", APP));
        }
    }

    @Test
    public void theGitHubRuleStillStandsWithTheSecondRuleArmed() {
        // 加第二条不是换第一条：官方发布路径照常可信，官方路径上的花活照常死。
        assertTrue(ReleasePlan.downloadUrlIsTrusted(GOOD_URL, "ai-assistant-native-0.15.apk", APP));
        assertFalse(ReleasePlan.downloadUrlIsTrusted(
                "https://github.com/abonla599/ai-assistant/releases/download/v0.15/evil.apk",
                "ai-assistant-native-0.15.apk", APP));
    }

    @Test
    public void decideAcceptsTheSelfHostedAssetOnlyInTheThreeArgShape() {
        String json = release("v0.15", SELF_URL, "ai-assistant-native-0.15.apk", 4_096L, null, false, false);
        ReleasePlan.Decision d = ReleasePlan.decide("0.14", json, APP);
        assertEquals(ReleasePlan.Kind.AVAILABLE, d.kind);
        assertEquals(SELF_URL, d.url);
        assertEquals(4_096L, d.sizeBytes);
        // 同一份 JSON，不交 APP_URL 的两参调用仍然按旧白名单拒——放行的永远是调用姿势不是内容
        assertEquals(ReleasePlan.Kind.UNUSABLE, ReleasePlan.decide("0.14", json).kind);
    }

    @Test
    public void theConstantMatchesTheRealPinnedDownloadAddress() throws Exception {
        // SELF_APK_PATH 不是装饰：它必须恰好是 build.gradle 里钉死的 UPDATE_APK_URL 的路径，
        // 且与 APP_URL 同 host。漂移的样子是"规则放行一个根本没人监听的地址"。
        // backend/tests/test_android_shell.py 从后端数那三个地址的同源；这条从壳这边数
        // 白名单常量与钉死地址的一致——两处各数各的，合起来没有缝。
        //
        // 找文件不赌工作目录：JVM 台架从仓库根跑（"android/app/build.gradle" 命中），
        // Gradle 的 testDebugUnitTest 在模块目录 android/app 里跑（"build.gradle" 命中）——
        // CI 第一次红就红在只认了前一种。候选逐个试，用【内容】认货：没有那两个
        // buildConfigField 的文件哪怕同名也不算找到，免得 root 的 android/build.gradle 混进来。
        StringBuilder sb = new StringBuilder();
        String[] candidates = {"android/app/build.gradle", "build.gradle",
                "../android/app/build.gradle"};
        for (String cand : candidates) {
            java.io.File f = new java.io.File(cand);
            if (!f.isFile()) {
                continue;
            }
            StringBuilder text = new StringBuilder();
            try (java.io.BufferedReader r = new java.io.BufferedReader(
                    new java.io.FileReader(f))) {
                String line;
                while ((line = r.readLine()) != null) text.append(line).append('\n');
            }
            if (text.indexOf("APP_URL") >= 0 && text.indexOf("UPDATE_APK_URL") >= 0) {
                sb.append(text);
                break;
            }
        }
        assertTrue("在 " + java.util.Arrays.toString(candidates) + " 里没找到钉死地址所在的 build.gradle（cwd="
                + new java.io.File("").getAbsolutePath() + "）", sb.length() > 0);
        java.util.regex.Matcher m = java.util.regex.Pattern.compile(
                "buildConfigField\\s+\"String\",\\s+\"(APP_URL|UPDATE_APK_URL)\",\\s+\"\\\\\"(.*?)\\\\\"\"")
                .matcher(sb);
        java.util.Map<String, String> pinned = new java.util.LinkedHashMap<>();
        while (m.find()) pinned.put(m.group(1), m.group(2));
        assertEquals("build.gradle 里的钉死地址少了字段：" + pinned.keySet(),
                2, pinned.size());
        java.net.URL apk = new java.net.URL(pinned.get("UPDATE_APK_URL"));
        java.net.URL app = new java.net.URL(pinned.get("APP_URL"));
        assertEquals("UPDATE_APK_URL 的路径不再是白名单常量那一个",
                ReleasePlan.SELF_APK_PATH, apk.getPath());
        assertTrue("白名单的第二条与钉死地址不同源",
                apk.getHost().equalsIgnoreCase(app.getHost()));
        // 拿钉死的真值走一遍完整判定：真链路必须被放行，这是规则与现实的接缝检查
        assertTrue(ReleasePlan.downloadUrlIsTrusted(pinned.get("UPDATE_APK_URL"),
                "ai-assistant-native-0.15.apk", pinned.get("APP_URL")));
    }

    // ---------- 校验值：形状不对等于没有 ----------

    private static final String DIGEST = "a" + "b".repeat(62) + "c"; // 64 位小写十六进制

    @Test
    public void anAvailableReleaseCarriesItsDigest() {
        ReleasePlan.Decision d = ReleasePlan.decide("0.14", releaseWithDigest(DIGEST));
        assertEquals(ReleasePlan.Kind.AVAILABLE, d.kind);
        assertEquals(DIGEST, d.sha256);
    }

    @Test
    public void anythingThatIsNotLowercaseHex64IsNoDigestAtAll() {
        String[] junk = {DIGEST.toUpperCase(), DIGEST.substring(1), DIGEST.substring(0, 63),
                DIGEST.substring(0, 32) + DIGEST.substring(0, 32).toUpperCase(),
                " " + DIGEST, "", null};
        for (String value : junk) {
            ReleasePlan.Decision d = ReleasePlan.decide("0.14", releaseWithDigest(value));
            assertEquals("校验值形状没被拒：" + value, null, d.sha256);
            // 缺校验值不改变"有没有新版"的判断——拒绝下载是下一层的事，且必须发生在动流量之前
            assertEquals(ReleasePlan.Kind.AVAILABLE, d.kind);
        }
    }

    @Test
    public void aReleaseWithoutTheDigestFieldIsStillReadAsARelease() {
        // 老 Release（v0.18 及以前）正文里根本没有那行；decide 照常给 AVAILABLE，
        // sha256 为 null 由 MainActivity 在点「下载」时拦下。
        ReleasePlan.Decision d = ReleasePlan.decide("0.14", release("v0.15", GOOD_URL,
                "ai-assistant-native-0.15.apk", 1L, null, false, false));
        assertEquals(ReleasePlan.Kind.AVAILABLE, d.kind);
        assertNull(d.sha256);
    }

    // ---------- 夹具 ----------

    /** 在发布对象顶层挂一个 apk_sha256 字段（服务端注入的那一份），值可以是任意形状的原样字符串。 */
    private static String releaseWithDigest(String digest) {
        Map<String, Object> rel = new LinkedHashMap<>();
        rel.put("tag_name", "v0.15");
        rel.put("draft", false);
        rel.put("prerelease", false);
        rel.put("body", "说明");
        Map<String, Object> asset = new LinkedHashMap<>();
        asset.put("name", "ai-assistant-native-0.15.apk");
        asset.put("browser_download_url", GOOD_URL);
        asset.put("size", 103_000L);
        List<Object> assets = new ArrayList<>();
        assets.add(asset);
        rel.put("assets", assets);
        if (digest != null) {
            rel.put("apk_sha256", digest);
        }
        return MiniJson.encode(rel);
    }

    private static String release(String tag, String url, String assetName, long size,
                                  String body, boolean draft, boolean prerelease) {
        Map<String, Object> rel = new LinkedHashMap<>();
        rel.put("tag_name", tag);
        rel.put("draft", draft);
        rel.put("prerelease", prerelease);
        rel.put("body", body);
        List<Object> assets = new ArrayList<>();
        if (url != null) {
            Map<String, Object> asset = new LinkedHashMap<>();
            asset.put("name", assetName);
            asset.put("browser_download_url", url);
            asset.put("size", size);
            assets.add(asset);
        }
        rel.put("assets", assets);
        return MiniJson.encode(rel);
    }
}
