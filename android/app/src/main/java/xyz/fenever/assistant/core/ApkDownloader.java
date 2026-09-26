package xyz.fenever.assistant.core;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;

/**
 * APK 下载的整条收字节路径（v0.23 T1.6）。一行 {@code android.*} 都不 import——
 * 与 {@link ReleasePlan} 同层同规：终点是"把可安装的字节放上磁盘"的判断，
 * 必须能在 JVM 台架上逐条钉住，而不是埋在 Activity 里等真机去撞。
 *
 * <p>从 {@code MainActivity.runApkDownload} 抽取时保留了那三件套的每一个零件，
 * 因为它们各自对着一段事故史（2026-09-23 残包）：
 * ① 流式收 + 体积边收边核——声明与实收双重上限，超过立刻断；
 * ② 临时文件 {@code *.part} + 完成后改名——安装页永远看不到半截文件；
 * ③ 落盘后 {@link ApkDigest} 与发布校验值对账——对不上就没有归位，也没有下一次误装的机会。
 * 失败路径统一清理 temp；清理失败不掩盖原始错误。
 *
 * <p>网络接缝由调用方注入（{@link Connector}）：真机上是 HttpURLConnection，
 * 台架上是内存字节。这一层因此不需要任何 Android 运行时就能把上面三条全测一遍。
 */
public final class ApkDownloader {

    /** 打开并定位到最终响应（重定向跟不跟由实现决定，与抽取前的 Activity 行为一致）。 */
    public interface Connector {
        Opened open(String url) throws IOException;
    }

    /** 一次已建立的上游响应：状态码、声明体积、字节流。 */
    public interface Opened {
        int status();
        long contentLength();
        InputStream body() throws IOException;
        void closeQuietly();
    }

    /** 进度回调。percent 为 0..100；总体积未知时 total 传 -1、percent 传已收字节数。 */
    public interface Progress {
        void onProgress(int percent, long done, long total);
    }

    /** 判定结果：{@code error == null} 即成功（target 就位可安装）。 */
    public static final class Result {
        public final String error;
        public final long bytesWritten;
        Result(String error, long bytesWritten) {
            this.error = error;
            this.bytesWritten = bytesWritten;
        }
        public boolean ok() { return error == null; }
    }

    private ApkDownloader() {}

    /**
     * 把 url 的字节流收进 temp，对账通过才改名成 target。
     *
     * @param url            钉死的下载地址（调用方负责它已过白名单——这里不复核，
     *                       免得"信任判断"出现第二处真相；白名单只在 {@link ReleasePlan}）
     * @param temp           {@code <name>.apk.part} 半截位
     * @param target         最终的 {@code ai-assistant-<version>.apk}
     * @param maxBytes       体积硬上限（与后端 releases.APK_MAX_BYTES 同一个数）
     * @param expectedSha256 发布校验值；null 直接拒——没有对账对象的下载就是上次事故的原样
     * @param progress       可空（台架/不需要进度的调用方）
     */
    public static Result download(String url, final File temp, final File target,
                                  long maxBytes, String expectedSha256,
                                  Connector connector, Progress progress) {
        if (expectedSha256 == null || expectedSha256.isEmpty()) {
            return new Result("这一版的发布里没有可信校验值，拒绝下载", 0L);
        }
        Opened opened = null;
        long done = 0L;
        try {
            opened = connector.open(url);
            if (opened.status() != 200) {
                // 上游取不到包时会 302 回发布页——跟着跳完拿到的就不是 APK 字节流。
                // 停在这里比"收下 HTML 再去校验"诚实，也更早给出对得上的失败文案。
                return new Result("服务回 HTTP " + opened.status(), 0L);
            }
            long declared = opened.contentLength();
            if (declared > maxBytes) {
                return new Result("声明的包体积异常", 0L);
            }
            InputStream in = opened.body();
            FileOutputStream out = new FileOutputStream(temp);
            try {
                byte[] buf = new byte[8192];
                int read;
                int lastPercent = -1;
                while ((read = in.read(buf)) > 0) {
                    done += read;
                    if (done > maxBytes) {
                        // 在 finally 关流之后还要清 temp：这里先标记，跳出循环统一处理
                        in.close();
                        out.flush();
                        out.close();
                        deleteQuietly(temp);
                        return new Result("下载超出体积上限", done);
                    }
                    out.write(buf, 0, read);
                    if (progress != null && declared > 0) {
                        int percent = (int) Math.min(100L, done * 100L / declared);
                        if (percent != lastPercent) {
                            lastPercent = percent;
                            progress.onProgress(percent, done, declared);
                        }
                    }
                }
            } finally {
                in.close();
                out.flush();
                out.close();
            }
            if (declared > 0 && temp.length() != declared) {
                deleteQuietly(temp);
                return new Result("下载中断：收下的字节比声明的少", done);
            }
            if (temp.length() == 0L) {
                // 0 字节的"合法摘要"也存在（sha256("")），空文件永远不该被当成包
                deleteQuietly(temp);
                return new Result("下载回来是个空文件", 0L);
            }
            String actual = ApkDigest.sha256Hex(temp);
            if (!expectedSha256.equals(actual)) {
                deleteQuietly(temp);   // 残包/被换过的包留在盘上只会喂给下一次误装
                return new Result("校验值不一致，这个包不是发布的那一份", done);
            }
            if (!temp.renameTo(target)) {
                return new Result("文件写好了却没归位（存储状态异常）", done);
            }
            return new Result(null, done);
        } catch (Exception e) {
            deleteQuietly(temp);
            return new Result("下载失败：" + e.getMessage(), done);
        } finally {
            if (opened != null) opened.closeQuietly();
        }
    }

    private static void deleteQuietly(File file) {
        try {
            if (file.isFile() && !file.delete()) {
                file.deleteOnExit();
            }
        } catch (SecurityException ignored) {
            file.deleteOnExit();       // 清理失败不掩盖原始错误，但也绝不留半成品
        }
    }
}
