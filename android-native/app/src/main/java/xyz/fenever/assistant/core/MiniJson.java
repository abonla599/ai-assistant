package xyz.fenever.assistant.core;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 极小 JSON。不用 org.json 是因为 android.jar 里那套是 stub，JVM 单测一调就抛 "Stub!"。 */
public final class MiniJson {
    private MiniJson() {}

    public static String encode(Object value) {
        StringBuilder sb = new StringBuilder();
        write(value, sb);
        return sb.toString();
    }

    private static void write(Object v, StringBuilder sb) {
        if (v == null) { sb.append("null"); return; }
        if (v instanceof String) { writeString((String) v, sb); return; }
        if (v instanceof Boolean) { sb.append(v); return; }
        if (v instanceof Integer || v instanceof Long) { sb.append(v); return; }
        if (v instanceof Double || v instanceof Float) { sb.append(((Number) v).doubleValue()); return; }
        if (v instanceof Map) {
            sb.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                if (!first) sb.append(',');
                first = false;
                writeString(String.valueOf(e.getKey()), sb);
                sb.append(':');
                write(e.getValue(), sb);
            }
            sb.append('}');
            return;
        }
        if (v instanceof List) {
            sb.append('[');
            boolean first = true;
            for (Object item : (List<?>) v) {
                if (!first) sb.append(',');
                first = false;
                write(item, sb);
            }
            sb.append(']');
            return;
        }
        throw new IllegalArgumentException("不支持编码的类型: " + v.getClass());
    }

    private static void writeString(String s, StringBuilder sb) {
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"' || c == '\\') sb.append('\\').append(c);
            else if (c == '\n') sb.append("\\n");
            else if (c == '\r') sb.append("\\r");
            else if (c == '\t') sb.append("\\t");
            else if (c < 0x20) sb.append(' ');   // 其余控制字符替换成空格，不生成 unicode 转义
            else sb.append(c);
        }
        sb.append('"');
    }

    public static Object decode(String text) {
        Parser p = new Parser(text);
        p.skipWs();
        Object value = p.value();
        p.skipWs();
        if (!p.done()) throw new IllegalArgumentException("JSON 尾部有多余内容");
        return value;
    }

    private static final class Parser {
        private final String s;
        private int i;

        Parser(String s) { this.s = s; }

        boolean done() { return i >= s.length(); }

        void skipWs() { while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++; }

        Object value() {
            if (done()) throw new IllegalArgumentException("JSON 意外结束");
            char c = s.charAt(i);
            if (c == '{') return object();
            if (c == '[') return array();
            if (c == '"') return string();
            if (c == 't') return literal("true", Boolean.TRUE);
            if (c == 'f') return literal("false", Boolean.FALSE);
            if (c == 'n') return literal("null", null);
            return number();
        }

        private Object literal(String word, Object value) {
            if (!s.startsWith(word, i)) throw new IllegalArgumentException("裸词不合法");
            i += word.length();
            return value;
        }

        private Map<String, Object> object() {
            Map<String, Object> out = new LinkedHashMap<>();
            i++;                       // {
            skipWs();
            if (peek() == '}') { i++; return out; }
            while (true) {
                skipWs();
                String key = string();
                skipWs();
                expect(':');
                skipWs();
                out.put(key, value());
                skipWs();
                char c = peek();
                if (c == ',') { i++; continue; }
                expect('}');
                return out;
            }
        }

        private List<Object> array() {
            List<Object> out = new ArrayList<>();
            i++;                       // [
            skipWs();
            if (peek() == ']') { i++; return out; }
            while (true) {
                skipWs();
                out.add(value());
                skipWs();
                char c = peek();
                if (c == ',') { i++; continue; }
                expect(']');
                return out;
            }
        }

        private Number number() {
            int start = i;
            while (!done() && "+-.eE0123456789".indexOf(s.charAt(i)) >= 0) i++;
            String raw = s.substring(start, i);
            if (raw.isEmpty()) throw new IllegalArgumentException("不是数字");
            if (raw.indexOf('.') >= 0 || raw.indexOf('e') >= 0 || raw.indexOf('E') >= 0)
                return Double.valueOf(raw);
            return Long.valueOf(raw);
        }

        private String string() {
            expect('"');
            StringBuilder sb = new StringBuilder();
            while (!done()) {
                char c = s.charAt(i++);
                if (c == '"') return sb.toString();
                if (c != '\\') { sb.append(c); continue; }
                char esc = s.charAt(i++);
                switch (esc) {
                    case '"': case '\\': case '/': sb.append(esc); break;
                    case 'n': sb.append('\n'); break;
                    case 'r': sb.append('\r'); break;
                    case 't': sb.append('\t'); break;
                    default: throw new IllegalArgumentException("不认的转义: \\" + esc);
                }
            }
            throw new IllegalArgumentException("字符串未闭合");
        }

        private char peek() {
            if (done()) throw new IllegalArgumentException("JSON 意外结束");
            return s.charAt(i);
        }

        private void expect(char c) {
            if (done() || s.charAt(i) != c) throw new IllegalArgumentException("期望 " + c);
            i++;
        }
    }
}
