package xyz.fenever.assistant.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/**
 * ExportName 对 Python 语义的镜像校验（v0.23 T2.4）。
 *
 * <p>期望值全部拿 backend/app/session/export_store.py 的 _safe_filename 真跑一遍
 * 抄录下来（含 NBSP/全角空格剥离、emoji 按码点截断这些 Java 原生 API 会给错答案的
 * 用例）。服务端清洗规则若改动，test_release_probe 的正则/常量钉死先红；这里是
 * 行为面的第二道——两道加起来才是"两端文件名不会悄悄分叉"的完整论证。
 */
public class ExportNameTest {

    @Test
    public void ordinaryTitlePassesThroughUntouched() {
        assertEquals("周末计划", ExportName.safeFilename("周末计划"));
        assertEquals("周末计划.md", ExportName.exportFileName("周末计划"));
    }

    @Test
    public void forbiddenCharactersBecomeSingleSpaces() {
        assertEquals("a b c d e f g h i",
                ExportName.safeFilename("a/b:c*d?e\"f<g>h|i"));
        // 连续禁用字符合并成一个空格（正则的 + 语义），不是逐个替换留一串洞
        assertEquals("x y", ExportName.safeFilename("x//y"));
        assertEquals("line1 line2", ExportName.safeFilename("line1\r\nline2"));
    }

    @Test
    public void pythonWhitespaceSetIsStrippedNotJavaApproximation() {
        assertEquals("hi", ExportName.safeFilename("  hi  "));
        // NBSP(U+00A0) 与全角空格(U+3000)：Python str.strip 认，Java String.strip 不认。
        // 这两条用例就是"不借道 Java strip"的存在理由。
        assertEquals("hi", ExportName.safeFilename("\u00A0hi\u00A0"));
        assertEquals("hi", ExportName.safeFilename("\u3000hi\u3000"));
    }

    @Test
    public void emptyAfterCleaningFallsBackToDialog() {
        assertEquals("对话", ExportName.safeFilename(""));
        assertEquals("对话", ExportName.safeFilename(null));
        assertEquals("对话", ExportName.safeFilename("  /:"));
        assertEquals("对话.md", ExportName.exportFileName(""));
    }

    @Test
    public void limitIsCountedInCodePointsNotUtf16Units() {
        String fifty = repeat("あ", 50);
        assertEquals(40, ExportName.safeFilename(fifty).length());

        String exactlyForty = repeat("周", 20) + repeat("末", 20);
        assertEquals(exactlyForty, ExportName.safeFilename(exactlyForty));

        // emoji 是代理对：按码元截会把刀口落在半个字符上，按码点截不会。
        String smileys = repeat("\uD83D\uDE42", 45);
        String cut = ExportName.safeFilename(smileys);
        assertEquals(40, cut.codePointCount(0, cut.length()));
        assertEquals(repeat("\uD83D\uDE42", 40), cut);
        assertFalse("截断处不许留下孤立代理对", Character.isHighSurrogate(
                cut.charAt(cut.length() - 1)));
    }

    // ---------- 票据路径形状门 ----------

    @Test
    public void theCanonicalTicketPathIsAccepted() {
        // token_urlsafe(16) 的产物形状：22 个 base64url 字符，恰好一段。
        assertTrue(ExportName.isTicketPath("/v1/exports/abc-_def-ghi_JKL-mno01"));
    }

    @Test
    public void anythingThatEscapesTheShapeIsRefused() {
        String[] rejected = {
                null, "", "/v1/exports/", "/v1/exports",
                "/v1/exports/abc-_def-ghi_JKL-mno0",    // 21 字符
                "/v1/exports/abc-_def-ghi_JKL-mno012",  // 23 字符
                "/v1/exports/abc+_def-ghi_JKL-mno01",   // 非法字符 +
                "/v1/exports/abc.def-ghi_JKL-mno01",    // 非法字符 .
                "/v1/exports/x/y",                      // 多出来的一段
                "/v1/exports/abc-_def-ghi_JKL-mno01#frag", // 锚点
                "https://evil.example/v1/exports/abc-_def-ghi_JKL-mno01", // 越境绝对地址
                "/v2/exports/abc-_def-ghi_JKL-mno01",   // 换了前缀
        };
        for (String p : rejected) {
            assertFalse("不该放行：" + p, ExportName.isTicketPath(p));
        }
    }

    // ---------- 夹具 ----------

    private static String repeat(String s, int n) {
        StringBuilder sb = new StringBuilder(s.length() * n);
        for (int i = 0; i < n; i++) {
            sb.append(s);
        }
        return sb.toString();
    }
}
