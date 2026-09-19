package xyz.fenever.assistant.core;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;

import org.junit.Test;

/** readShareChunk 的入参：一个 JSON 字符串，length 在这里就被夹到 512KB。 */
public class ChunkRequestTest {

    @Test public void readsIdOffsetAndLength() {
        ChunkRequest c = ChunkRequest.parse(
                "{\"id\":\"s0123456789ab\",\"offset\":1024,\"length\":4096}");
        assertNotNull(c);
        assertEquals("s0123456789ab", c.id);
        assertEquals(1024, c.offset);
        assertEquals(4096, c.length);
    }

    /**
     * JS 每一块都按 512KB 问（最后一块要的比剩下的多），所以超限是【夹】不是【拒】：
     * 拒了最后一块就永远读不出来，用户看到的是「这张图怎么也上传不上去」。
     */
    @Test public void clampsAnOversizedLengthInsteadOfRefusingIt() {
        ChunkRequest c = ChunkRequest.parse(
                "{\"id\":\"s0123456789ab\",\"offset\":0,\"length\":67108864}");
        assertEquals(ChunkRequest.MAX_LENGTH, c.length);
        assertEquals(512 * 1024, ChunkRequest.MAX_LENGTH);
    }

    @Test public void rejectsIdsThatAreNotWhitelisted() {
        assertNull(ChunkRequest.parse("{\"id\":\"../etc/passwd\",\"offset\":0,\"length\":10}"));
        assertNull(ChunkRequest.parse("{\"id\":\"short\",\"offset\":0,\"length\":10}"));
        assertNull(ChunkRequest.parse("{\"id\":\"r-abc');alert(1);//\",\"offset\":0,\"length\":10}"));
        assertNull(ChunkRequest.parse("{\"offset\":0,\"length\":10}"));
    }

    @Test public void rejectsImpossibleOffsetsAndLengths() {
        assertNull(ChunkRequest.parse("{\"id\":\"s0123456789ab\",\"offset\":-1,\"length\":10}"));
        assertNull(ChunkRequest.parse("{\"id\":\"s0123456789ab\",\"offset\":0,\"length\":0}"));
        assertNull(ChunkRequest.parse("{\"id\":\"s0123456789ab\",\"offset\":0,\"length\":-8}"));
        assertNull(ChunkRequest.parse("{\"id\":\"s0123456789ab\",\"length\":10}"));
        // 超出 int 的偏移量不可能读得到，与其回绕成一个小负数不如什么都不给
        assertNull(ChunkRequest.parse(
                "{\"id\":\"s0123456789ab\",\"offset\":2147483648,\"length\":10}"));
    }

    @Test public void rejectsAnythingThatIsNotOneJsonObject() {
        assertNull(ChunkRequest.parse(null));
        assertNull(ChunkRequest.parse(""));
        assertNull(ChunkRequest.parse("s0123456789ab"));           // 位置参数那种写法
        assertNull(ChunkRequest.parse("[\"s0123456789ab\",0,10]"));
        assertNull(ChunkRequest.parse("{\"id\":\"s0123456789ab\",\"offset\":0,\"length\":10,}"));
    }
}
