package xyz.fenever.assistant.core;

import java.util.Map;

/**
 * {@code readShareChunk(json)} 的入参清洗。
 *
 * <p>参数是【一个 JSON 字符串】{@code {"id":...,"offset":...,"length":...}}，
 * 不是三个位置参数（spec §2 的初稿把它写成三个位置参数，那是错的，@JavascriptInterface
 * 的方法我们只给它一个 String）。
 *
 * <p>{@code length} 在这里就被夹到 512KB（spec §2 铁律③）：JS 每一块都按 512KB 问，
 * 最后一块要的比剩下的多，所以是【夹】不是【拒】——拒了最后那块就永远读不出来。
 */
public final class ChunkRequest {

    public static final int MAX_LENGTH = 512 * 1024;

    public final String id;
    public final int offset;
    /** 已经过 {@link #MAX_LENGTH} 夹裁，一定是 1..512KB。 */
    public final int length;

    private ChunkRequest(String id, int offset, int length) {
        this.id = id;
        this.offset = offset;
        this.length = length;
    }

    /** 坏 JSON、不是对象、id 不过白名单、offset/length 不是非负整数 —— 回 null。 */
    public static ChunkRequest parse(String json) {
        if (json == null || json.trim().isEmpty()) return null;
        Object decoded;
        try {
            decoded = MiniJson.decode(json);
        } catch (RuntimeException e) {
            return null;
        }
        if (!(decoded instanceof Map)) return null;
        Map<?, ?> row = (Map<?, ?>) decoded;

        Object rawId = row.get("id");
        if (!(rawId instanceof String) || !IDs.valid((String) rawId)) return null;

        long offset = asLong(row.get("offset"));
        long length = asLong(row.get("length"));
        if (offset < 0 || length <= 0) return null;
        if (offset > Integer.MAX_VALUE || length > Integer.MAX_VALUE) return null;

        return new ChunkRequest((String) rawId, (int) offset, (int) Math.min(length, MAX_LENGTH));
    }

    private static long asLong(Object v) {
        if (v instanceof Number) return ((Number) v).longValue();
        return -1L;
    }
}
