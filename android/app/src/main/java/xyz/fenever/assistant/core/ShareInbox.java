package xyz.fenever.assistant.core;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * 系统分享进来的文件队列：字节落 {@code dir/<id>}，元数据落 {@link Io}（生产是 SharedPreferences）。
 *
 * <p>两类件走同一套规则：{@link #put} 收别人给的文件流，{@link #putText} 把一段纯文本
 * 也落成一个小文件。id、30 分钟 TTL、孤儿认领、consume 即删、512KB 分块对两者完全相同，
 * 所以网页只有一套代码。
 *
 * <p>不 import 任何 {@code android.*}，所以能在 CI 里跑真 JVM 单测。{@code dir} 由调用方给
 * （生产传 {@code context.getCacheDir()/shares}），路径永远是自己拼的，绝不接受外部路径片段。
 *
 * <p>owner 隔离只管【可见性】（{@link #pending()}）：id 是壳生成的随机串，只有拿到它的
 * 那个网页读得走字节，所以 {@link #chunk} 与 {@link #consume} 只认 id——
 * 这不是漏口，而是 {@code storesBytesUnderItsOwnPathAndReadsBackChunks} 这条测试钉住的行为
 * （它没 setOwner 也要能分块读回）。
 */
public final class ShareInbox {

    public interface Io {
        String read();
        void write(String content);
    }

    /** 与 spec §2 铁律 2 一致：id 走白名单，路径穿越与错长度一律拒。正则只在 {@link IDs} 定义一次。 */
    private static final Pattern ID_RE = IDs.PATTERN;

    /**
     * 对齐 backend/app/core/uploads.py 的 10MB；超了直接不收。
     *
     * <p>public 是给 {@link SharePolicy}（分享入口的前置闸门）引用：同一个数字写两处，
     * 迟早有一处会漂，漂了就成了「入口收得下、队列说太大」这种只在真机上半夜出现的错。
     */
    public static final long MAX_BYTES = 10L * 1024 * 1024;

    /**
     * 纯文本分享的闸门，对齐 backend/app/core/uploads.py:26 的 {@code MAX_TEXT_BYTES = 1MB}
     * ——文本/代码那条通道就收 1MB，给到 10MB 只会让一段超长笔记在服务端被截断或拒收，
     * 而拒收发生在上传那一步时，用户看到的是「发了个附件然后报错」。
     */
    public static final long MAX_TEXT_BYTES = 1024L * 1024L;

    /** 文本件的类型：与清单 intent-filter 里那一条 {@code text/plain} 同一个字面值。 */
    public static final String TEXT_MIME = "text/plain";

    /** 分享件 30 分钟即弃：没登录就被分享进来、或用户切走了不看，都不该一直占着 cache。 */
    private static final long TTL_MILLIS = 30L * 60_000L;

    /** 单次分块的上限，与桥的 512KB 同数；这里是夹一刀，拒绝由桥那一层报给 JS。 */
    private static final int MAX_CHUNK = 512 * 1024;

    private static final int BUFFER = 8192;

    private final File dir;
    private final Io io;
    private final List<Entry> entries = new ArrayList<>();
    private String owner;

    private static final class Entry {
        String id;
        String owner;
        String name;
        String mime;
        long size;
        long addedAt;
    }

    public ShareInbox(File dir, Io io) {
        this.dir = dir;
        this.io = io;
        load();
    }

    // ---------------------------------------------------------------- 持久化

    private synchronized void load() {
        String raw = io.read();
        if (raw == null || raw.trim().isEmpty()) return;
        Object decoded;
        try {
            decoded = MiniJson.decode(raw);
        } catch (RuntimeException e) {
            return;                       // 读不出来就当空表，下一次写会覆盖掉坏数据
        }
        if (!(decoded instanceof Map)) return;
        Map<?, ?> root = (Map<?, ?>) decoded;
        owner = root.get("owner") instanceof String ? (String) root.get("owner") : null;
        Object list = root.get("items");
        if (!(list instanceof List)) return;
        for (Object row : (List<?>) list) {
            if (!(row instanceof Map)) continue;
            Map<?, ?> m = (Map<?, ?>) row;
            String id = str(m.get("id"));
            if (id == null || !ID_RE.matcher(id).matches()) continue;
            Entry e = new Entry();
            e.id = id;
            e.owner = str(m.get("owner"));
            e.name = strOr(m.get("name"), "");
            e.mime = strOr(m.get("mime"), "application/octet-stream");
            e.size = m.get("size") instanceof Number ? ((Number) m.get("size")).longValue() : 0L;
            e.addedAt = m.get("addedAt") instanceof Number ? ((Number) m.get("addedAt")).longValue() : 0L;
            entries.add(e);
        }
        pruneMissingFiles();   // 系统清过 cache 就别说自己还有货
    }

    private synchronized void flush() {
        Map<String, Object> root = new LinkedHashMap<>();
        root.put("owner", owner);
        List<Object> rows = new ArrayList<>();
        for (Entry e : entries) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", e.id);
            row.put("owner", e.owner);
            row.put("name", e.name);
            row.put("mime", e.mime);
            row.put("size", e.size);
            row.put("addedAt", e.addedAt);
            rows.add(row);
        }
        root.put("items", rows);
        io.write(MiniJson.encode(root));
    }

    private static String str(Object v) { return v instanceof String ? (String) v : null; }

    private static String strOr(Object v, String fallback) {
        return v instanceof String ? (String) v : fallback;
    }

    // ---------------------------------------------------------------- 归属

    public synchronized void setOwner(String user) {
        owner = (user == null || user.isEmpty()) ? null : user;
        if (owner != null) {
            // 未登录时收到的分享件原先不归任何人（spec §4 的 null 归属），
            // 而 pendingIsEmptyUntilAnOwnerIsSet 要求登录之后它就能看见——
            // 所以第一次有主时把这些孤儿认领过来，先到先得，之后不再改。
            for (Entry e : entries) if (e.owner == null) e.owner = owner;
        }
        flush();
    }

    public synchronized String activeOwner() { return owner; }

    // ---------------------------------------------------------------- 入队

    public boolean put(String id, InputStream in, String name, String mime, long size) {
        return put(id, in, name, mime, size, System.currentTimeMillis());
    }

    /**
     * 纯文本分享：把一段 {@code EXTRA_TEXT} 落成一个 text/plain 小文件。
     *
     * <p>别的 App 分享文字时基本只给 {@code EXTRA_TEXT}、不给 {@code EXTRA_STREAM}，
     * 走 {@link #put} 那条路等于把这类分享全拒了。这里刻意【不另开一条只读文本的旁路】：
     * 转成字节后交给同一个 {@code put}，于是 id 白名单、30 分钟 TTL、孤儿认领、
     * consume 即删、分块读这些规则对文本与图片是同一套，网页那边也一套代码。
     *
     * <p>按 UTF-8 的【字节数】把 1MB 这道闸（{@link #MAX_TEXT_BYTES}），不是按 char 数：
     * 汉字 3 字节，后端截断与拒收看的都是字节。
     */
    public boolean putText(String id, String text, String name) {
        return putText(id, text, name, System.currentTimeMillis());
    }

    /** 返回 false = 文本为空、字节数超 1MB、或 id 非法（{@link #put} 那套判据一个不落地都用上）。 */
    public synchronized boolean putText(String id, String text, String name, long addedAt) {
        if (text == null) return false;
        byte[] bytes = text.getBytes(StandardCharsets.UTF_8);
        if (bytes.length == 0 || bytes.length > MAX_TEXT_BYTES) return false;
        return put(id, new ByteArrayInputStream(bytes), name, TEXT_MIME, bytes.length, addedAt);
    }

    /** 返回 false = id 非法、声明体积超 10MB、或流本身读不完/读超。失败不留半成品文件。 */
    public synchronized boolean put(String id, InputStream in, String name, String mime,
                                    long size, long addedAt) {
        sweepNow();
        if (in == null || id == null || !ID_RE.matcher(id).matches()) return false;
        if (size < 0 || size > MAX_BYTES) return false;
        File target = file(id);
        if (target == null) return false;
        Entry e = new Entry();
        e.id = id;
        e.owner = owner;
        e.name = safeName(name);
        e.mime = (mime == null || mime.isEmpty()) ? "application/octet-stream" : mime;
        e.addedAt = addedAt;
        long written = 0;
        byte[] buf = new byte[BUFFER];
        try {
            FileOutputStream out = new FileOutputStream(target);
            try {
                while (true) {
                    int n = in.read(buf);
                    if (n < 0) break;
                    written += n;
                    if (written > MAX_BYTES) {          // 声明的 size 撒了谎，以实际字节为准
                        out.close();
                        deleteFile(target);
                        return false;
                    }
                    out.write(buf, 0, n);
                }
            } finally {
                closeQuietly(out);
            }
        } catch (IOException ex) {
            deleteFile(target);
            return false;
        }
        e.size = written;
        replaceEntry(id);                                // 同 id 重来：旧文件与旧记录一起换掉
        entries.add(e);
        sort();
        flush();
        return true;
    }

    private void replaceEntry(String id) {
        for (int i = 0; i < entries.size(); i++) {
            if (entries.get(i).id.equals(id)) {
                deleteFile(file(id));
                entries.remove(i);
                return;
            }
        }
    }

    // ---------------------------------------------------------------- 出货

    public synchronized List<Map<String, Object>> pending() {
        sweepNow();
        List<Map<String, Object>> out = new ArrayList<>();
        if (owner == null) return out;                   // fail-closed
        for (Entry e : entries) {
            if (!owner.equals(e.owner)) continue;
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", e.id);
            row.put("name", e.name);
            row.put("mime", e.mime);
            row.put("size", e.size);
            out.add(row);
        }
        return out;
    }

    /** 越界/没有这个文件一律回空数组，不抛异常——桥那边把它翻成 {"b64":""}。 */
    public synchronized byte[] chunk(String id, int offset, int length) {
        sweepNow();
        if (id == null || !ID_RE.matcher(id).matches() || offset < 0 || length <= 0)
            return new byte[0];
        File f = file(id);
        if (f == null) return new byte[0];
        int want = Math.min(length, MAX_CHUNK);
        try (RandomAccessFile raf = new RandomAccessFile(f, "r")) {
            long available = raf.length() - offset;
            if (available <= 0) return new byte[0];
            int n = (int) Math.min(want, available);
            byte[] buf = new byte[n];
            raf.seek(offset);
            raf.readFully(buf);
            return buf;
        } catch (IOException ex) {
            return new byte[0];
        }
    }

    /** 消费即删：JS 拿到字节、拼成 Blob 传上后端之后就调它。没这个 id 回 false。 */
    public synchronized boolean consume(String id) {
        sweepNow();
        if (id == null || !ID_RE.matcher(id).matches()) return false;
        for (int i = 0; i < entries.size(); i++) {
            if (entries.get(i).id.equals(id)) {
                entries.remove(i);
                deleteFile(file(id));
                flush();
                return true;
            }
        }
        return false;
    }

    /** 每次公开调用开头自己跑一遍；Task 7 也会在前台回来时显式调一次。 */
    public synchronized void sweepExpired(long nowMillis) {
        sweepFrom(nowMillis);
    }

    private void sweepNow() {
        sweepFrom(System.currentTimeMillis());
    }

    private void sweepFrom(long nowMillis) {
        boolean changed = false;
        for (int i = entries.size() - 1; i >= 0; i--) {
            Entry e = entries.get(i);
            boolean stale = nowMillis - e.addedAt > TTL_MILLIS;
            File f = file(e.id);
            boolean gone = f == null || !f.exists();
            if (!stale && !gone) continue;
            if (stale) deleteFile(f);
            entries.remove(i);
            changed = true;
        }
        if (changed) flush();
    }

    /** 文件被系统清掉了就别把幽灵记录留给 JS：读到 size 却没字节比直接看不见更糟。 */
    private void pruneMissingFiles() {
        boolean changed = false;
        for (int i = entries.size() - 1; i >= 0; i--) {
            File f = file(entries.get(i).id);
            if (f == null || !f.exists()) {
                entries.remove(i);
                changed = true;
            }
        }
        if (changed) flush();
    }

    private void sort() {
        Collections.sort(entries, new Comparator<Entry>() {
            public int compare(Entry a, Entry b) { return Long.compare(a.addedAt, b.addedAt); }
        });
    }

    // ---------------------------------------------------------------- 文件

    /** 只有校验过的 id 才会拼进路径；dir 建不出来时回 null 表示"这台机器上没地方放"。 */
    private File file(String id) {
        if (id == null || !ID_RE.matcher(id).matches()) return null;
        if (!dir.isDirectory() && !dir.mkdirs()) return null;
        return new File(dir, id);
    }

    private static void deleteFile(File f) {
        if (f != null && f.exists() && !f.delete()) f.deleteOnExit();
    }

    private static void closeQuietly(java.io.Closeable c) {
        if (c == null) return;
        try {
            c.close();
        } catch (IOException ignored) {
            // 关不上就算了：文件要么已写完，要么调用方马上会删掉它
        }
    }

    /**
     * 文件名是外部可控字符串（分享它的 App 起的），只用来在界面上显示，
     * 所以先把路径分隔符砍掉、控制字符换成空格、限长 128。
     * 它【不】参与拼路径——路径只由 id 决定。
     */
    private static String safeName(String raw) {
        if (raw == null || raw.isEmpty()) return "shared";
        String s = raw.replace('\\', '/');
        int slash = s.lastIndexOf('/');
        if (slash >= 0) s = s.substring(slash + 1);
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < s.length() && sb.length() < 128; i++) {
            char c = s.charAt(i);
            sb.append(c < 0x20 ? ' ' : c);
        }
        String trimmed = sb.toString().trim();
        return trimmed.isEmpty() ? "shared" : trimmed;
    }
}
