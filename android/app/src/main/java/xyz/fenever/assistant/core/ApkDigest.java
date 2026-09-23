package xyz.fenever.assistant.core;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/**
 * 文件内容的 SHA-256，小写十六进制。只用 JDK API——壳的零第三方依赖纪律到这一层同样生效。
 *
 * <p>存在的唯一理由：更新链路要把"下载回来的字节"与"发布信息里的校验值"对一次，
 * 对不上就不许有任何安装页被拉起。这个换算必须能在 JVM 台架上被测（known-answer 测试），
 * 所以从 Activity 里收出来这一层，而不是一团埋在 UI 代码里等真机去撞。
 */
public final class ApkDigest {

    private ApkDigest() {}

    /** 流读完即关；返回 64 位小写十六进制。 */
    public static String sha256Hex(InputStream in) throws IOException {
        MessageDigest digest;
        try {
            digest = MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            // SHA-256 是 JCA 的必选算法；真到这一步说明运行时被换过了，绝不能"算了就算过"
            throw new IOException("这台设备算不了 SHA-256", e);
        }
        try {
            byte[] buf = new byte[8192];
            int read;
            while ((read = in.read(buf)) > 0) {
                digest.update(buf, 0, read);
            }
        } finally {
            in.close();
        }
        return toHex(digest.digest());
    }

    public static String sha256Hex(File file) throws IOException {
        return sha256Hex(new FileInputStream(file));
    }

    private static final char[] HEX = "0123456789abcdef".toCharArray();

    private static String toHex(byte[] bytes) {
        char[] out = new char[bytes.length * 2];
        for (int i = 0; i < bytes.length; i++) {
            int v = bytes[i] & 0xFF;
            out[i * 2] = HEX[v >>> 4];
            out[i * 2 + 1] = HEX[v & 0x0F];
        }
        return new String(out);
    }
}
