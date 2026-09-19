package xyz.fenever.assistant.core;

import static org.junit.Assert.*;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.Test;

public class MiniJsonTest {
    @Test public void encodesStringsAndEscapesTheTwoThatMatter() {
        assertEquals("\"a\\\"b\\\\c\"", MiniJson.encode("a\"b\\c"));
    }

    @Test public void encodesNumbersBoolsAndNull() {
        // 计划此处写的是 assertEquals("1.0", MiniJson.encode(1L))，但计划自己给的实现
        // 对 Long 输出 "1"，而且下面 roundTripsAnObjectArray 要求 Long 原样往返（编成
        // "1842311.0" 就会 decode 成 Double 而失配）。两种说法只能留一个：整数按实现走，
        // "1.0" 归给真正会产生它的 Double——这样 Long 与 Double 两条路径都被钉住。
        assertEquals("1", MiniJson.encode(1L));
        assertEquals("1.0", MiniJson.encode(1.0));
        assertEquals("true", MiniJson.encode(Boolean.TRUE));
        assertEquals("null", MiniJson.encode(null));
    }

    @Test public void roundTripsAnObjectArray() {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("id", "s-abc12345678");
        row.put("size", 1842311L);
        row.put("ok", true);
        String json = MiniJson.encode(Arrays.asList(row, row));
        List<?> back = (List<?>) MiniJson.decode(json);
        assertEquals(2, back.size());
        Map<?, ?> m = (Map<?, ?>) back.get(0);
        assertEquals("s-abc12345678", m.get("id"));
        assertEquals(1842311L, m.get("size"));
    }

    @Test public void controlCharsInValuesDoNotBreakTheDocument() {
        String json = MiniJson.encode("tab\tnewline\n");
        assertEquals("tab\tnewline\n", MiniJson.decode(json));
    }
}
