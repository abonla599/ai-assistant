package xyz.fenever.assistant.core;

import static org.junit.Assert.assertEquals;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

import org.junit.Test;

/**
 * SHA-256 换算的 known-answer 测试。
 *
 * <p>不测"跑没跑出个字符串"，测的是**这几个字节必须折成这一个摘要**：摘要算错时的表现
 * 是每一次正常下载都被判成"校验值不一致"——更新链路整体锁死，而这恰恰是校验存在的理由
 * （宁可不装也不装错）反过来咬人的形状。NIST 的abc向量与空向量是外部事实，改不动。
 */
public class ApkDigestTest {

    private static final String ABC =
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
    private static final String EMPTY =
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

    @Test
    public void knownAnswerVectorsForTheAbcStreamAndTheEmptyStream() throws Exception {
        assertEquals(ABC, ApkDigest.sha256Hex(stream("abc")));
        assertEquals(EMPTY, ApkDigest.sha256Hex(stream("")));
    }

    @Test
    public void aFileOnDiskHashesToTheSameValueAsTheSameBytesAsStream() throws Exception {
        File file = File.createTempFile("apkdigest", ".bin");
        try {
            FileOutputStream out = new FileOutputStream(file);
            try {
                out.write("abc".getBytes(StandardCharsets.UTF_8));
            } finally {
                out.close();
            }
            assertEquals(ABC, ApkDigest.sha256Hex(file));
        } finally {
            file.delete();
        }
    }

    /** 分块边界不许影响结果：真实下载是 8 KB 一块地喂进来的。 */
    @Test
    public void chunkingDoesNotChangeTheDigest() throws Exception {
        byte[] payload = new byte[100_000];
        for (int i = 0; i < payload.length; i++) {
            payload[i] = (byte) (i * 31 + 7);
        }
        String whole = ApkDigest.sha256Hex(new ByteArrayInputStream(payload));
        // 与 JDK 现算对照：这条钉的是 ApkDigest 的拼装/关流/十六进制，不重复发明轮子
        java.security.MessageDigest md = java.security.MessageDigest.getInstance("SHA-256");
        StringBuilder sb = new StringBuilder();
        for (byte b : md.digest(payload)) {
            sb.append(String.format("%02x", b));
        }
        assertEquals(sb.toString(), whole);
    }

    private static InputStream stream(String text) {
        return new ByteArrayInputStream(text.getBytes(StandardCharsets.UTF_8));
    }
}
