# 壳三项原生能力（v0.13）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给现有 WebView 壳加上本地提醒、系统分享入口、桌面组件三项原生能力，后端只改一行响应头。

**Architecture:** 全部新代码落在 `android/`（9 个新类 + 3 个资源文件），零第三方依赖；原生与网页之间只有一条 `@JavascriptInterface` 白名单桥。纯逻辑（排期表、分享队列、JSON 编解码）收进不 import 任何 `android.*` 的 `core/` 子包，好在 CI 里跑真正的 JVM 单测。

**Tech Stack:** Java 17、Android 平台 API（AlarmManager / NotificationManager / AppWidgetProvider / DownloadManager 已有）、JUnit 4（仅测试期依赖）、FastAPI（一行 CSP）、原生 ES JS（无构建步骤）。

**Spec:** `docs/superpowers/specs/2026-09-19-shell-native-capabilities-design.md` —— 计划与 spec 一起读；spec 里已记每条裁决的理由，本计划不再重复。

## Global Constraints

每个任务都隐含遵守下面全部。数值逐条抄自 spec，不要就地改：

- **零第三方依赖**：`android/app/build.gradle` 里不得出现 `implementation`（测试用 `testImplementation` 是唯一例外，见 Task 2）。
- **SDK 档位**：`minSdk 23`、`targetSdk 34`、`compileSdk 34`、`sourceCompatibility/targetCompatibility = JavaVersion.VERSION_17`。
- **权限只加两项**：`android.permission.POST_NOTIFICATIONS`（运行时）、`android.permission.RECEIVE_BOOT_COMPLETED`（安装期）。**不申请** `SCHEDULE_EXACT_ALARM`，不加前台服务，不做厂商白名单引导。
- **闹钟 API 固定为** `AlarmManager.setAndAllowWhileIdle(RTC_WAKEUP, at, pi)`；提醒允许晚几分钟，产品不承诺准点。
- **桥对象名** `window.AssistantShell`，八个方法，全部同步返回 JSON 字符串：`capabilities` `setOwner` `scheduleReminder` `cancelReminder` `listReminders` `pendingShares` `readShareChunk` `consumeShare`。方法**只加不减不改语义**。
- **桥绝不接触会话令牌**，绝不把外部字符串拼进 `evaluateJavascript`（只传 id）。
- **id 正则**：`^[A-Za-z0-9_-]{8,24}$`。路径永远由壳自己拼成 `cacheDir/shares/<校验过的 id>`。
- **上限**：每 owner 32 条提醒；`readShareChunk` 单次 `length` ≤ 512KB；分享件 ≤ 10MB（对齐 `backend/app/core/uploads.py:27`），消费后或 30 分钟即删。
- **owner 隔离 fail-closed**：未调 `setOwner` 前，`listReminders()` / `pendingShares()` 一律返回空数组；到点通知只发给 `activeOwner`。
- **时间约定**：`at` 是 epoch 毫秒，由 JS 用设备本地时区算好再传；壳不做任何日历运算。
- **JS 降级**：`window.AssistantShell` 不存在时（浏览器直接访问、headless Edge + CDP）一切照旧，提醒栏出说明文案，分享队列恒空。
- **提交前型检**：任何 `.java` 改动先过 `javac` + `android.jar`（命令见 Task 1 之外的 Task 5「型检台」一节）。退出码非 0 就不许提交。

## 并行分派

- **Wave 1（互不依赖，可同时开工）**：Task 1（后端 CSP）、Task 2（纯 Java core + MiniJson）、Task 3（CI 工作流）、Task 4（前端 shell.js）。
- **Wave 2（依赖 Task 2 的类签名，可与 Task 4 并行但彼此共享 `MainActivity.java`/`AndroidManifest.xml`，必须串行）**：Task 5 → Task 6 → Task 7 → Task 8。
- **Wave 3**：Task 9（版本与发布），要前面全绿。

Task 5-8 都要改 `MainActivity.java` 和 `AndroidManifest.xml`，**不要并行派**，否则互相覆盖。

---

## File Structure

```
android/app/src/main/java/xyz/fenever/assistant/
  core/MiniJson.java        新：极简 JSON 编解码。不 import android.*
  core/Reminder.java        新：提醒记录（不可变字段 + 可变 at）。不 import android.*
  core/ReminderStore.java   新：排期表 + owner 隔离 + 上限 + repeat 推进。不 import android.*
  core/ShareInbox.java      新：待分享件队列 + id 校验 + 过期清理。不 import android.*
  ShellBridge.java          新：唯一注入 JS 世界的对象
  NotificationChannels.java 新：一次性建渠道
  ReminderScheduler.java    新：往 AlarmManager 排/撤
  ReminderReceiver.java     新：到点 → 发通知 + 推进 repeat + 刷组件
  BootReceiver.java         新：开机重排
  ShareActivity.java        新：ACTION_SEND 跳板
  AssistantWidget.java      新：AppWidgetProvider
  MainActivity.java         改：装桥、收冷启动 extra、onPageFinished 冲事件
android/app/src/main/res/layout/widget_assistant.xml    新
android/app/src/main/res/xml/widget_assistant_info.xml  新
android/app/src/main/res/xml/shortcuts.xml              新
android/app/src/test/java/xyz/fenever/assistant/core/   新：JVM 单测
backend/app/web/web_router.py   改：一行 CSP
backend/app/web/static/shell.js 新：桥适配层 + 无桥降级
backend/app/web/static/index.html 改：加一个 script 标签
backend/app/web/static/app.js   改：三处接线（afterAuth / 设置项 / 事件回调）
backend/app/web/static/sw.js    改：缓存名 v7 → v8
.github/workflows/android-tests.yml 新
```

---

### Task 1: 给 `/app` 加 CSP，堵住跨源 iframe 拿桥

**Files:**
- Modify: `backend/app/web/web_router.py:48-51`（`RevalidatingStaticFiles.get_response`）
- Test: `backend/tests/test_route_auth_contract.py`（追加）

**Interfaces:**
- Consumes: 无
- Produces: `/app/*` 的每个响应带 `Content-Security-Policy: frame-src 'none'`

- [ ] **Step 1: 写失败测试**

追加到 `backend/tests/test_route_auth_contract.py` 末尾：

```python
def test_app_shell_refuses_frames():
    """壳把 @JavascriptInterface 挂到每个 frame 的 window 上，而 origin 校验只看主文档。
    同源 XSS 只要能插入第三方 iframe 就能绕过校验——这一行 CSP 是从 web 侧堵它。"""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    for path in ("/app/", "/app/app.js", "/app/style.css"):
        headers = client.get(path).headers
        assert headers.get("content-security-policy") == "frame-src 'none'", path
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_route_auth_contract.py::test_app_shell_refuses_frames -q`
Expected: FAIL，`assert None == "frame-src 'none'"`

- [ ] **Step 3: 实现**

在 `backend/app/web/web_router.py` 的 `get_response` 里，`Cache-Control` 那行后面加一行：

```python
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        # 全站零 iframe（2026-09-19 grep 确认），所以这条不会碰坏任何东西；
        # 它挡的是"WebView 里 @JavascriptInterface 会挂到每个 frame"这条路。
        response.headers["Content-Security-Policy"] = "frame-src 'none'"
        return response
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_route_auth_contract.py -q`
Expected: 全 PASS（含既有路由契约测试）

- [ ] **Step 5: 提交**

```bash
git add backend/app/web/web_router.py backend/tests/test_route_auth_contract.py
git commit -m "给 /app 加 frame-src 'none'：堵住跨源 iframe 拿到原生桥"
```

---

### Task 2: 纯 Java 内核（MiniJson / Reminder / ReminderStore / ShareInbox）+ JVM 单测

**Files:**
- Create: `android/app/src/main/java/xyz/fenever/assistant/core/MiniJson.java`
- Create: `android/app/src/main/java/xyz/fenever/assistant/core/Reminder.java`
- Create: `android/app/src/main/java/xyz/fenever/assistant/core/ReminderStore.java`
- Create: `android/app/src/main/java/xyz/fenever/assistant/core/ShareInbox.java`
- Create: `android/app/src/test/java/xyz/fenever/assistant/core/MiniJsonTest.java`
- Create: `android/app/src/test/java/xyz/fenever/assistant/core/ReminderStoreTest.java`
- Create: `android/app/src/test/java/xyz/fenever/assistant/core/ShareInboxTest.java`
- Modify: `android/app/build.gradle`（加 `testImplementation` 与 junit 版本）

**Interfaces:**
- Consumes: 无（这四个类不 import 任何 `android.*`，只用 `java.*` 与 `java.io`）
- Produces（Task 5-8 按这些签名调用，一字不差）：
  - `MiniJson.encode(Object) -> String`，`MiniJson.decode(String) -> Object`（`Map`/`List`/`String`/`Double`/`Long`/`Boolean`/`null`）
  - `new Reminder(String id, String owner, long at, String title, String body, String repeat)`，字段全 public final，`at` 非 final
  - `ReminderStore.new ReminderStore(Io io)`；`interface ReminderStore.Io { String read(); void write(String content); }`
  - `ReminderStore.setOwner(String) -> void`、`activeOwner() -> String`（可为 null）
  - `ReminderStore.add(Reminder) -> boolean`（false = 该 owner 已满 32 条）
  - `ReminderStore.cancel(String id) -> boolean`
  - `ReminderStore.list() -> List<Reminder>`（只含 `activeOwner` 的）
  - `ReminderStore.dueAt(long nowMillis) -> List<Reminder>`（到点且属于 activeOwner）
  - `ReminderStore.advance(Reminder, long nowMillis) -> void`（once 删除；daily/weekly 推到下一个未来时刻）
  - `ShareInbox.new ShareInbox(File dir, Io io)`；`put(String id, InputStream in, String name, String mime, long size) -> boolean`（false = 超 10MB 或 id 非法）
  - `ShareInbox.pending() -> List<Map<String,Object>>`、`chunk(String id, int offset, int length) -> byte[]`、`consume(String id) -> boolean`、`sweepExpired(long nowMillis) -> void`

- [ ] **Step 1: 加测试依赖**

`android/app/build.gradle` 的 `android { ... }` 块**之后**追加（`testImplementation` 只进测试 classpath，不进 APK，不违反零依赖）：

```gradle
dependencies {
    testImplementation "junit:junit:4.13.2"
}
```

- [ ] **Step 2: 写 MiniJson 的失败测试**

`android/app/src/test/java/xyz/fenever/assistant/core/MiniJsonTest.java`：

```java
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
        assertEquals("1.0", MiniJson.encode(1L));
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
```

- [ ] **Step 3: 实现 MiniJson**

要点：encode 支持 `Map`/`List`/`String`/`Long`/`Integer`/`Double`/`Boolean`/`null`；decode 只认这几种，遇到 `[]{}":,` 之外的裸词只允许 `true/false/null`；字符串里的 `\u` 直接拒绝（我们从不生成它）。解析失败抛 `IllegalArgumentException`。

```java
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
            else if (c < 0x20) sb.append(' ');   // 其余控制字符替换成空格，不生成 \u
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
```

- [ ] **Step 4: 跑 MiniJson 测试**

Run: `cd android && gradle :app:testDebugUnitTest --tests "*MiniJsonTest*"`（本机无 SDK 时跳过，由 CI 跑；至少要用下面的 javac 台子过一遍语法）
Expected: PASS

- [ ] **Step 5: 写 ReminderStore 的失败测试**

`android/app/src/test/java/xyz/fenever/assistant/core/ReminderStoreTest.java`：

```java
package xyz.fenever.assistant.core;

import static org.junit.Assert.*;

import java.util.List;
import org.junit.Test;

public class ReminderStoreTest {

    /** 内存假盘：生产用 SharedPreferences，测试用这个。 */
    private static final class MemIo implements ReminderStore.Io {
        String blob = "";
        public String read() { return blob; }
        public void write(String content) { blob = content; }
    }

    private static Reminder r(String id, String owner, long at, String repeat) {
        return new Reminder(id, owner, at, "标题", "正文", repeat);
    }

    @Test public void nothingIsVisibleBeforeAnOwnerIsSet() {
        ReminderStore store = new ReminderStore(new MemIo());
        store.add(r("r-aaaaaaaaaaaa", "alice", 1000L, "once"));
        assertTrue("没 setOwner 就不该看见任何东西", store.list().isEmpty());
        assertTrue(store.dueAt(2000L).isEmpty());
    }

    @Test public void oneOwnerNeverSeesAnotherOwnersReminder() {
        ReminderStore store = new ReminderStore(new MemIo());
        store.add(r("r-aaaaaaaaaaaa", "alice", 1000L, "once"));
        store.add(r("r-bbbbbbbbbbbb", "bob", 1500L, "once"));
        store.setOwner("alice");
        assertEquals(1, store.list().size());
        assertEquals("alice", store.list().get(0).owner);
    }

    @Test public void dueOnlyReturnsPastOrPresentForTheActiveOwner() {
        ReminderStore store = new ReminderStore(new MemIo());
        store.setOwner("alice");
        store.add(r("r-aaaaaaaaaaaa", "alice", 1000L, "once"));
        store.add(r("r-bbbbbbbbbbbb", "alice", 9000L, "once"));
        List<Reminder> due = store.dueAt(5000L);
        assertEquals(1, due.size());
        assertEquals("r-aaaaaaaaaaaa", due.get(0).id);
    }

    @Test public void perOwnerCapIsThirtyTwo() {
        ReminderStore store = new ReminderStore(new MemIo());
        store.setOwner("alice");
        for (int i = 0; i < 32; i++) {
            String id = String.format("r-%012d", i);
            assertTrue("第 " + i + " 条不该被拒", store.add(r(id, "alice", 1000L + i, "once")));
        }
        assertFalse("第 33 条必须被拒", store.add(r("r-overflow0001", "alice", 9999L, "once")));
        assertEquals(32, store.list().size());
    }

    @Test public void onceDisappearsAfterFiringButDailyRollsForward() {
        ReminderStore store = new ReminderStore(new MemIo());
        store.setOwner("alice");
        store.add(r("r-aaaaaaaaaaaa", "alice", 1000L, "once"));
        store.add(r("r-bbbbbbbbbbbb", "alice", 1000L, "daily"));
        store.advance(store.byId("r-aaaaaaaaaaaa"), 2000L);
        store.advance(store.byId("r-bbbbbbbbbbbb"), 2000L);
        assertNull(store.byId("r-aaaaaaaaaaaa"));
        Reminder daily = store.byId("r-bbbbbbbbbbbb");
        assertEquals(1000L + 24 * 3600_000L, daily.at);
    }

    @Test public void aLateDailyRollsToTheNextFutureSlotNotBackfillingHistory() {
        ReminderStore store = new ReminderStore(new MemIo());
        store.setOwner("alice");
        long day = 24 * 3600_000L;
        store.add(r("r-aaaaaaaaaaaa", "alice", 1000L, "daily"));
        store.advance(store.byId("r-aaaaaaaaaaaa"), 1000L + 5 * day);   // 迟了五轮
        assertEquals(1000L + 6 * day, store.byId("r-aaaaaaaaaaaa").at);
    }

    @Test public void survivesReopenBecauseEveryWriteHitsDisk() {
        MemIo io = new MemIo();
        ReminderStore first = new ReminderStore(io);
        first.setOwner("alice");
        first.add(r("r-aaaaaaaaaaaa", "alice", 1000L, "once"));
        ReminderStore reopened = new ReminderStore(io);
        reopened.setOwner("alice");
        assertEquals(1, reopened.list().size());
        assertEquals("r-aaaaaaaaaaaa", reopened.list().get(0).id);
    }

    @Test public void cancelRemovesAndReportsWhetherItExisted() {
        ReminderStore store = new ReminderStore(new MemIo());
        store.setOwner("alice");
        store.add(r("r-aaaaaaaaaaaa", "alice", 1000L, "once"));
        assertTrue(store.cancel("r-aaaaaaaaaaaa"));
        assertFalse(store.cancel("r-aaaaaaaaaaaa"));
        assertTrue(store.list().isEmpty());
    }
}
```

- [ ] **Step 6: 实现 ReminderStore**

要点：`add`/`cancel`/`advance`/`setOwner` 都以 `synchronized` 保护并**立刻落盘**（`io.write(encode())`）；`encode` 用 `MiniJson`，顶层是对象 `{"owner": "...", "items": [ ... ]}`；`owner` 字段也持久化，这样重启后不用 JS 再报一次也能恢复（但 `setOwner` 仍会覆盖它）。列表按 `at` 升序排。`byId` 是测试与内部用的查找。

```java
package xyz.fenever.assistant.core;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 排期表。不 import android.*，所以能在 CI 里跑真 JVM 单测。 */
public final class ReminderStore {
    public interface Io {
        String read();
        void write(String content);
    }

    private static final int MAX_PER_OWNER = 32;
    private static final long DAY = 24L * 3600_000L;
    private static final long WEEK = 7L * DAY;

    private final Io io;
    private final List<Reminder> items = new ArrayList<>();
    private String owner;

    public ReminderStore(Io io) {
        this.io = io;
        load();
    }

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
            String own = str(m.get("owner"));
            if (id == null || own == null || !(m.get("at") instanceof Number)) continue;
            items.add(new Reminder(id, own, ((Number) m.get("at")).longValue(),
                    strOr(m.get("title"), ""), strOr(m.get("body"), ""),
                    strOr(m.get("repeat"), "once")));
        }
    }

    private static String str(Object v) { return v instanceof String ? (String) v : null; }

    private static String strOr(Object v, String fallback) {
        return v instanceof String ? (String) v : fallback;
    }

    private synchronized void flush() {
        Map<String, Object> root = new LinkedHashMap<>();
        root.put("owner", owner);
        List<Object> rows = new ArrayList<>();
        for (Reminder r : items) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", r.id);
            row.put("owner", r.owner);
            row.put("at", r.at);
            row.put("title", r.title);
            row.put("body", r.body);
            row.put("repeat", r.repeat);
            rows.add(row);
        }
        root.put("items", rows);
        io.write(MiniJson.encode(root));
    }

    public synchronized void setOwner(String user) {
        owner = (user == null || user.isEmpty()) ? null : user;
        flush();
    }

    public synchronized String activeOwner() { return owner; }

    public synchronized boolean add(Reminder reminder) {
        if (reminder == null || reminder.owner == null) return false;
        if (countFor(reminder.owner) >= MAX_PER_OWNER) return false;
        items.add(reminder);
        sort();
        flush();
        return true;
    }

    private int countFor(String user) {
        int n = 0;
        for (Reminder r : items) if (user.equals(r.owner)) n++;
        return n;
    }

    private void sort() {
        Collections.sort(items, new Comparator<Reminder>() {
            public int compare(Reminder a, Reminder b) { return Long.compare(a.at, b.at); }
        });
    }

    public synchronized boolean cancel(String id) {
        boolean removed = false;
        for (int i = 0; i < items.size(); i++) {
            if (items.get(i).id.equals(id)) { items.remove(i); removed = true; i--; }
        }
        if (removed) flush();
        return removed;
    }

    public synchronized List<Reminder> list() { return visibleForOwner(); }

    public synchronized List<Reminder> dueAt(long nowMillis) {
        List<Reminder> out = new ArrayList<>();
        for (Reminder r : visibleForOwner()) if (r.at <= nowMillis) out.add(r);
        return out;
    }

    private List<Reminder> visibleForOwner() {
        List<Reminder> out = new ArrayList<>();
        if (owner == null) return out;                 // fail-closed
        for (Reminder r : items) if (owner.equals(r.owner)) out.add(r);
        return out;
    }

    public synchronized Reminder byId(String id) {
        for (Reminder r : items) if (r.id.equals(id)) return r;
        return null;
    }

    /** once 删除；daily/weekly 推到下一个【未来】时刻，不补发错过的轮次。 */
    public synchronized void advance(Reminder reminder, long nowMillis) {
        if (reminder == null) return;
        if ("daily".equals(reminder.repeat) || "weekly".equals(reminder.repeat)) {
            long step = "daily".equals(reminder.repeat) ? DAY : WEEK;
            long next = reminder.at;
            while (next <= nowMillis) next += step;
            reminder.at = next;
        } else {
            items.remove(reminder);
        }
        sort();
        flush();
    }
}
```

- [ ] **Step 7: 写并实现 ShareInbox 的失败测试**

`android/app/src/test/java/xyz/fenever/assistant/core/ShareInboxTest.java`：

```java
package xyz.fenever.assistant.core;

import static org.junit.Assert.*;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.nio.file.Files;
import java.util.List;
import java.util.Map;
import org.junit.Before;
import org.junit.Test;

public class ShareInboxTest {
    private File dir;

    @Before public void freshDir() throws Exception {
        dir = Files.createTempDirectory("shares").toFile();
    }

    private static ShareInbox.Io memIo() {
        return new ShareInbox.Io() {
            String blob = "";
            public String read() { return blob; }
            public void write(String content) { blob = content; }
        };
    }

    private static ByteArrayInputStream data(String s) {
        return new ByteArrayInputStream(s.getBytes("UTF-8"));
    }

    @Test public void rejectsIdsWithPathTraversalOrWrongLength() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        assertFalse(inbox.put("../etc/passwd", data("x"), "n", "image/png", 1));
        assertFalse(inbox.put("short", data("x"), "n", "image/png", 1));
        assertFalse(inbox.put("has space12345", data("x"), "n", "image/png", 1));
    }

    @Test public void storesBytesUnderItsOwnPathAndReadsBackChunks() throws Exception {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        assertTrue(inbox.put("s0123456789ab", data("hello world"), "a.png", "image/png", 11));
        assertArrayEquals("hello".getBytes("UTF-8"), inbox.chunk("s0123456789ab", 0, 5));
        assertArrayEquals("world".getBytes("UTF-8"), inbox.chunk("s0123456789ab", 6, 5));
        assertEquals(0, inbox.chunk("s0123456789ab", 99, 5).length);
    }

    @Test public void refusesAnythingOverTenMegabytes() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        assertFalse(inbox.put("s0123456789ac", data("x"), "big.png", "image/png",
                10L * 1024 * 1024 + 1));
    }

    @Test public void pendingIsEmptyUntilAnOwnerIsSet() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        inbox.put("s0123456789ab", data("x"), "a.png", "image/png", 1);
        assertTrue(inbox.pending().isEmpty());
        inbox.setOwner("alice");
        assertEquals(1, inbox.pending().size());
        assertEquals("a.png", inbox.pending().get(0).get("name"));
    }

    @Test public void consumeDeletesTheFileAndReportsMisses() throws Exception {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        inbox.setOwner("alice");
        inbox.put("s0123456789ab", data("x"), "a.png", "image/png", 1);
        assertTrue(inbox.consume("s0123456789ab"));
        assertFalse(inbox.consume("s0123456789ab"));
        assertEquals(0, dir.listFiles().length);
    }

    @Test public void sweepDropsEntriesOlderThanThirtyMinutes() {
        ShareInbox inbox = new ShareInbox(dir, memIo());
        inbox.setOwner("alice");
        long t0 = 1_700_000_000_000L;
        inbox.put("s0123456789ab", data("x"), "a.png", "image/png", 1, t0);
        inbox.sweepExpired(t0 + 31L * 60_000L);
        assertTrue(inbox.pending().isEmpty());
    }

    @Test public void listingSurvivesReopen() {
        ShareInbox.Io io = memIo();
        ShareInbox first = new ShareInbox(dir, io);
        first.setOwner("alice");
        first.put("s0123456789ab", data("x"), "a.png", "image/png", 1);
        ShareInbox reopened = new ShareInbox(dir, io);
        reopened.setOwner("alice");
        List<Map<String, Object>> rows = reopened.pending();
        assertEquals(1, rows.size());
        assertEquals(1L, rows.get(0).get("size"));
    }
}
```

实现要点：`ShareInbox.Io` 与 `ReminderStore.Io` 同形状（读/写一个字符串）；`put` 用 8KB 缓冲流复制，**不把整块读进内存**；文件路径固定 `new File(dir, id)`，`dir` 由调用方给（生产传 `context.getCacheDir()/shares`）；`pending()` 返回的每个 map 含 `id/name/mime/size`，按 `addedAt` 升序；`chunk` 越界返回空数组而不是抛异常；`sweepExpired` 在每次公开方法开头自动跑一次。

- [ ] **Step 8: 本地型检 + 提交**

四个 core 类不 import android，可以直接用 JDK 编译跑通：

```bash
cd /c/Users/34426/ai-assistant/android/app/src/main/java && javac -encoding UTF-8 -d /tmp/coreout xyz/fenever/assistant/core/*.java && echo "型检 OK"
```

```bash
git add android/app/build.gradle android/app/src/main/java/xyz/fenever/assistant/core android/app/src/test
git commit -m "壳的纯 Java 内核：MiniJson、排期表、分享队列（不碰 android.*，可在 CI 跑 JVM 单测）"
```

> **为什么自己写 JSON**：`android.jar` 里的 `org.json` 是 stub，JVM 上 `new JSONObject()` 直接抛 `"Stub!"`。用它就等于"测试跑不过、只能靠 CI"，而本任务的全部意义就是在本地/CI 的纯 JVM 里验。

---

### Task 3: CI 工作流 `android-tests.yml`

**Files:**
- Create: `.github/workflows/android-tests.yml`

**Interfaces:**
- Consumes: Task 2 的测试目录（`android/app/src/test/**`）
- Produces: `assembleDebug` + `testDebugUnitTest` 两个 job 步骤，push 到 `android/**` 时跑

- [ ] **Step 1: 写工作流**

```yaml
name: Android Shell Tests

# 为什么单开一个文件而不是并进 release-apk.yml：那个由 tag 触发，出包前必须先有
# 测试可跑；并在一起会让"测试失败"和"发版失败"混成同一次红，事后分不清是哪类。
on:
  push:
    paths:
      - "android/**"
      - ".github/workflows/android-tests.yml"
  pull_request:
    paths:
      - "android/**"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: "17"
      - uses: gradle/actions/setup-gradle@v4
        with:
          gradle-version: "8.7"
      - name: Unit tests (pure JVM)
        run: gradle -p android --no-daemon --stacktrace testDebugUnitTest
      - name: Assemble debug APK
        run: gradle -p android --no-daemon --stacktrace assembleDebug
```

- [ ] **Step 2: 本地 YAML 自检**

Run: `python -c "import yaml,sys;yaml.safe_load(open('.github/workflows/android-tests.yml',encoding='utf-8'));print('yaml ok')"`
Expected: `yaml ok`

- [ ] **Step 3: 提交**

```bash
git add .github/workflows/android-tests.yml
git commit -m "CI 加 android-tests.yml：出包前先跑 JVM 单测与 assembleDebug"
```

---

### Task 4: 前端 `shell.js` 适配层与无桥降级

**Files:**
- Create: `backend/app/web/static/shell.js`
- Modify: `backend/app/web/static/index.html`（脚本区，`app.js` 之前）
- Modify: `backend/app/web/static/app.js`（`afterAuth` 约 490 行、`openSettings` 约 1265 行、启动处）
- Modify: `backend/app/web/static/sw.js`（`CACHE` 常量与 `SHELL` 数组）
- Test: `backend/tests/test_web_pwa.py`（追加语料锁）

**Interfaces:**
- Consumes: 桥的八个方法（Global Constraints 里列名）
- Produces: 全局 `SHELL` 对象，供 app.js 调用：
  - `SHELL.present`（boolean）、`SHELL.setOwner(user)`、`SHELL.listReminders() -> Array`
  - `SHELL.addReminder({at,title,body,repeat}) -> {ok,error?}`、`SHELL.cancelReminder(id) -> {ok}`
  - `SHELL.pendingShares() -> Array`、`SHELL.readShare(id) -> Blob`、`SHELL.consumeShare(id)`
  - `SHELL.onEvent(cb)` —— 只接收 `{type:"share"|"reminder", id}` 这种**只有 id** 的对象

- [ ] **Step 1: 写失败的语料锁测试**

追加到 `backend/tests/test_web_pwa.py`（沿用该文件已有的读静态文件与 `_code_lines` 工具）：

```python
def test_the_bridge_surface_is_locked_to_eight_methods():
    """桥的方法只加不减不改语义；名字漂了老壳会静默少功能，所以锁成语料。"""
    src = _code_lines("shell.js")
    for name in ("capabilities", "setOwner", "scheduleReminder", "cancelReminder",
                 "listReminders", "pendingShares", "readShareChunk", "consumeShare"):
        assert name in src, f"shell.js 里找不到桥方法 {name}"


def test_shell_degrades_without_the_bridge():
    """浏览器直接开网址、以及 headless Edge 跑 CDP 时没有 AssistantShell。"""
    src = _code_lines("shell.js")
    assert "window.AssistantShell" in src
    assert "present" in src
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest backend/tests/test_web_pwa.py -q -k bridge or degrade`
Expected: FAIL（`shell.js` 还不存在）

- [ ] **Step 3: 实现 shell.js**

```javascript
/* 原生壳的适配层。
 * 浏览器里没有 AssistantShell 时全部方法退化成"什么都不做"，
 * 这样同一份 JS 既服务 APK 也服务直接访问网址的人（和 CDP 验收）。
 */
const SHELL = (() => {
  const B = window.AssistantShell;
  const present = typeof B === "object" && B !== null;
  const listeners = [];

  function call(name, arg) {
    if (!present) return { ok: false, error: "no-shell" };
    try { return JSON.parse(B[name](arg === undefined ? "" : arg)) || {}; }
    catch (e) { return { ok: false, error: String(e) }; }
  }

  function decode(b64) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }

  return {
    present,
    setOwner(user) { if (present) B.setOwner(String(user || "")); },
    listReminders() {
      if (!present) return [];
      try { return JSON.parse(B.listReminders()) || []; } catch (e) { return []; }
    },
    addReminder(reminder) {
      return call("scheduleReminder", JSON.stringify(reminder));
    },
    cancelReminder(id) { return call("cancelReminder", String(id)); },
    pendingShares() {
      if (!present) return [];
      try { return JSON.parse(B.pendingShares()) || []; } catch (e) { return []; }
    },
    /* 分块取字节：一张 8MB 照片不该一次性穿过桥。上限由壳定（512KB/块）。 */
    async readShare(id) {
      if (!present) return null;
      const meta = this.pendingShares().find((s) => s.id === id);
      if (!meta) return null;
      const parts = [];
      for (let offset = 0; offset < meta.size; offset += 512 * 1024) {
        const chunk = call("readShareChunk", JSON.stringify({ id, offset, length: 512 * 1024 }));
        if (!chunk.b64) return null;              // 文件被系统清了或已过期
        parts.push(decode(chunk.b64));
      }
      return new Blob(parts, { type: meta.mime || "application/octet-stream" });
    },
    consumeShare(id) { return call("consumeShare", String(id)); },
    onEvent(cb) { listeners.push(cb); },
  };
})();

/* 壳只会推 {type, id}：内容一律不回传，避免把外部可控字符串拼进 JS。
 * 监听器数组放在 IIFE 外面，好让下面这个全局回调和 SHELL.onEvent 用同一份。 */
const SHELL_LISTENERS = [];
window.__shellEvent = (payload) => {
  let evt;
  try { evt = JSON.parse(payload); } catch (e) { return; }
  if (!evt || typeof evt.id !== "string") return;      // 只认带 id 的事件
  SHELL_LISTENERS.forEach((cb) => { try { cb(evt); } catch (e) {} });
};
```

并把 IIFE 里的 `const listeners = [];` 删掉、`onEvent(cb) { listeners.push(cb); }` 改成
`onEvent(cb) { SHELL_LISTENERS.push(cb); }`。

- [ ] **Step 4: 接线 app.js**

`afterAuth(res)` 内、`addIdentity(res)` 之后加一行（身份一确定就告诉壳"现在是谁"）：

```javascript
  SHELL.setOwner(res.user_id);
```

`loadWho()` 成功后同样调一次（冷启动已登录的情况）。`switchTo(userId)` 里也要调。

在设置弹层的列表里加一项"提醒"，页面渲染函数命名 `renderReminders()`，放在 `renderAccounts` 旁边；无桥时只出说明文案：

```javascript
function renderReminders(host) {
  host.innerHTML = "";
  if (!SHELL.present) {
    const note = document.createElement("p");
    note.className = "set-note";
    note.textContent = "这里设的提醒只在这台手机的应用里生效。";
    host.appendChild(note);
    return;
  }
  // 有桥：列出 SHELL.listReminders()，每行一个"取消"按钮（class set-mini set-del），
  // 顶部一个"添加提醒"表单：标题、时间（input type=datetime-local）、重复（once/daily/weekly）。
  // 提交时 new Date(value).getTime() 换 epoch 毫秒再交给 SHELL.addReminder。
}
```

分享入口：启动与收到 `SHELL.onEvent` 时各拉一次 `SHELL.pendingShares()`，有货就依次 `readShare(id)` → 走现有 `POST /v1/uploads` → `consumeShare(id)`。

- [ ] **Step 5: 加脚本标签并升缓存名**

`index.html` 里 `app.js` 那行之前插入：

```html
  <script src="shell.js"></script>
```

`sw.js`：`const CACHE = "ai-assistant-shell-v8";`，并把 `"shell.js"` 加进 `SHELL` 数组。

- [ ] **Step 6: 跑测试确认通过**

Run: `python -m pytest backend/tests/test_web_pwa.py -q`
Expected: 全 PASS

- [ ] **Step 7: 提交**

```bash
git add backend/app/web/static/shell.js backend/app/web/static/index.html backend/app/web/static/app.js backend/app/web/static/sw.js backend/tests/test_web_pwa.py
git commit -m "前端 shell.js：桥适配层 + 无桥降级 + 提醒设置页"
```

---

### Task 5: ShellBridge + MainActivity 装桥 + 通知渠道

**依赖 Task 2（调用 core 类）与 Task 4（约定事件形状）。**

**Files:**
- Create: `android/app/src/main/java/xyz/fenever/assistant/ShellBridge.java`
- Create: `android/app/src/main/java/xyz/fenever/assistant/NotificationChannels.java`
- Create: `android/app/src/main/java/xyz/fenever/assistant/PrefsIo.java`
- Modify: `android/app/src/main/java/xyz/fenever/assistant/MainActivity.java`
- Modify: `android/app/src/main/AndroidManifest.xml`（`POST_NOTIFICATIONS`）

**Interfaces:**
- Consumes: Task 2 的 `ReminderStore` / `ShareInbox` 签名；Task 4 约定的 `window.__shellEvent('{"type":"share","id":"..."}')`
- Produces:
  - `ShellBridge.attach(WebView)`、`ShellBridge.event(String json)`（主线程安全，页面未就绪则排队）
  - `NotificationChannels.ensure(Context)`、`NotificationChannels.REMINDERS = "assistant_reminders"`

- [ ] **Step 1: 记住型检台（每次改完 .java 都要跑）**

```bash
cd /c/Users/34426/.qoder-cn/tmp/android-platform
cp -r /c/Users/34426/ai-assistant/android/app/src/main/java/xyz/fenever/assistant src/xyz/fenever/
# src/xyz/fenever/assistant/BuildConfig.java 是手写桩（只给 APP_URL），别覆盖它
javac -encoding UTF-8 -classpath android-34-ext12/android.jar -d out \
      $(find src/xyz/fenever/assistant -name '*.java')
```

Expected: 退出码 0。抓不到资源引用/清单合并/运行时行为，那部分只有 CI 与实机能说。

- [ ] **Step 2: 实现 NotificationChannels**

```java
package xyz.fenever.assistant;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;

/** 渠道只在 Android 8+ 存在；建一次就长期留着，重复建是幂等的。 */
public final class NotificationChannels {
    public static final String REMINDERS = "assistant_reminders";

    private NotificationChannels() {}

    public static void ensure(Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        if (manager == null) return;
        NotificationChannel channel = new NotificationChannel(
                REMINDERS, "助手提醒", NotificationManager.IMPORTANCE_DEFAULT);
        channel.setDescription("到点的学习与任务提醒");
        manager.createNotificationChannel(channel);
    }
}
```

- [ ] **Step 3: 实现 ShellBridge**

八个方法全部 `@JavascriptInterface`、全部同步返回 JSON 字符串。三条硬规则（见 spec §2）：origin 校验、id 正则、不接触令牌。

```java
package xyz.fenever.assistant;

import android.net.Uri;
import android.util.Base64;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import xyz.fenever.assistant.core.MiniJson;
import xyz.fenever.assistant.core.Reminder;
import xyz.fenever.assistant.core.ReminderStore;
import xyz.fenever.assistant.core.ShareInbox;

public final class ShellBridge {
    private static final Pattern ID = Pattern.compile("^[A-Za-z0-9_-]{8,24}$");
    private static final int MAX_CHUNK = 512 * 1024;
    // 与 BuildConfig.APP_URL 同一个主机名。写错一个字母的后果是桥永远返回 origin 拒绝，
    // 而界面看起来"就是没有提醒功能"。
    private static final List<String> HOSTS = Arrays.asList("ai.fenever.xyz");

    private final WebView webview;
    private final ReminderStore reminders;
    private final ShareInbox shares;

    public ShellBridge(WebView webview, ReminderStore reminders, ShareInbox shares) {
        this.webview = webview;
        this.reminders = reminders;
        this.shares = shares;
    }

    /** 只有我们自己的页面能调桥。注意：iframe 场景由 CSP frame-src 'none' 兜，
     *  这里只看主文档 URL，挡不住同源页里插入的第三方 frame。 */
    private boolean sameOrigin() {
        Uri url = Uri.parse(String.valueOf(webview.getUrl()));
        return "https".equalsIgnoreCase(url.getScheme()) && HOSTS.contains(url.getHost());
    }

    private static String json(Map<String, Object> map) { return MiniJson.encode(map); }

    private Map<String, Object> refused(String why) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", false);
        out.put("error", why);
        return out;
    }

    @JavascriptInterface
    public String capabilities() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("shell", 1L);
        return json(out);
    }

    @JavascriptInterface
    public String setOwner(String user) {
        if (!sameOrigin() || user == null || user.length() > 64) return json(refused("origin"));
        reminders.setOwner(user);
        shares.setOwner(user);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", true);
        return json(out);
    }
}
```

剩下四个方法逐个写死，不要"参照上面"：

```java
    @JavascriptInterface
    public String cancelReminder(String id) {
        if (!sameOrigin() || !ID.matcher(safe(id)).matches()) return json(refused("origin"));
        boolean removed = reminders.cancel(id);
        ReminderScheduler.cancel(activity, id);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", removed);
        return json(out);
    }

    @JavascriptInterface
    public String listReminders() {
        if (!sameOrigin()) return "[]";                 // 拒绝时也返回空数组，不给探测信号
        List<Object> rows = new ArrayList<>();
        for (Reminder r : reminders.list()) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", r.id); row.put("at", r.at); row.put("title", r.title);
            row.put("repeat", r.repeat);
            rows.add(row);
        }
        return MiniJson.encode(rows);                   // 不含 body：列表页用不到正文
    }

    @JavascriptInterface
    public String pendingShares() {
        if (!sameOrigin()) return "[]";
        return MiniJson.encode(new ArrayList<Object>(shares.pending()));
    }

    @JavascriptInterface
    public String consumeShare(String id) {
        if (!sameOrigin() || !ID.matcher(safe(id)).matches()) return json(refused("origin"));
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", shares.consume(id));
        return json(out);
    }
```

`safe(String)` 只是 `s == null ? "" : s`，避免 `null` 进正则。`scheduleReminder(String json)`
解析 `{id,at,title,body,repeat}`：`id` 必须过 `ID`，`title`/`body` 截到 120/500 字符，
`repeat` 只认 `once|daily|weekly`（其它一律按 `once`），`add` 返回 false 时给
`{"ok":false,"error":"too_many"}`；成功后立刻 `ReminderScheduler.schedule(...)` 并把
`nextAt` 回给 JS。`readShareChunk` 接 `{"id","offset","length"}`，`length` 夹到 `MAX_CHUNK`，
返回 `{"b64": Base64.encodeToString(bytes, Base64.NO_WRAP)}`。

- [ ] **Step 4: PrefsIo —— core 层与 Android 之间唯一的接缝**

`ReminderStore.Io` 与 `ShareInbox.Io` 都是"读一个字符串 / 写一个字符串"。生产实现落在
`SharedPreferences` 上（`apply()` 异步落盘，够用且不会在桥线程里同步写盘）：

```java
package xyz.fenever.assistant;

import android.content.Context;
import android.content.SharedPreferences;
import xyz.fenever.assistant.core.ReminderStore;
import xyz.fenever.assistant.core.ShareInbox;

/** 把 core 层的"读一段/写一段"接到 SharedPreferences 上。 */
public final class PrefsIo {
    private PrefsIo() {}

    public static ReminderStore.Io reminders(Context context) {
        final SharedPreferences prefs = context.getSharedPreferences("reminders", Context.MODE_PRIVATE);
        return new ReminderStore.Io() {
            public String read() { return prefs.getString("blob", ""); }
            public void write(String content) { prefs.edit().putString("blob", content).apply(); }
        };
    }

    public static ShareInbox.Io shares(Context context) {
        final SharedPreferences prefs = context.getSharedPreferences("shares_meta", Context.MODE_PRIVATE);
        return new ShareInbox.Io() {
            public String read() { return prefs.getString("blob", ""); }
            public void write(String content) { prefs.edit().putString("blob", content).apply(); }
        };
    }
}
```

- [ ] **Step 5: MainActivity 装桥，并把冷启动参数交给网页**

在 `settings.setJavaScriptCanOpenWindowsAutomatically(false)` 之后、`webview.setWebViewClient(...)` 之前：

```java
        NotificationChannels.ensure(this);
        ReminderStore reminderStore = new ReminderStore(PrefsIo.reminders(this));
        ShareInbox shareInbox = new ShareInbox(new File(getCacheDir(), "shares"), PrefsIo.shares(this));
        final ShellBridge bridge = new ShellBridge(webview, reminderStore, shareInbox);
        webview.addJavascriptInterface(bridge, "AssistantShell");
        bridge.queueStartupEvent(getIntent());   // 冷启动带来的 pending_share / open_from
```

并在 `WebViewClient` 里加 `onPageFinished`，把排队的事件冲出去（页面没加载完就
`evaluateJavascript` 会静默丢失，通知点了没反应就是这么来的）：

```java
            @Override
            public void onPageFinished(WebView view, String url) {
                bridge.flushPendingEvents();
            }
```

`ShellBridge` 侧配套两个方法（同属本任务）：

```java
    /** 只认 id 与固定枚举值，绝不把外部文本拼进 JS。 */
    public void queueStartupEvent(android.content.Intent intent) {
        String share = intent.getStringExtra("pending_share");
        if (share != null && ID.matcher(share).matches()) enqueue("{\"type\":\"share\",\"id\":\"" + share + "\"}");
        String from = intent.getStringExtra("open_from");
        if ("camera".equals(from) || "new_chat".equals(from)) enqueue("{\"type\":\"open\",\"id\":\"" + from + "\"}");
        if (from != null && from.startsWith("reminder:") && ID.matcher(from.substring(9)).matches())
            enqueue("{\"type\":\"reminder\",\"id\":\"" + from.substring(9) + "\"}");
    }

    public void flushPendingEvents() {
        for (final String payload : drain()) {
            webview.post(() -> webview.evaluateJavascript(
                    "window.__shellEvent && window.__shellEvent('" + payload + "')", null));
        }
    }
```

`enqueue/drain` 用一个 `ArrayList<String>` + `synchronized`；`ID` 复用同一个正则。

- [ ] **Step 6: 型检 + 提交**

跑 Step 1 的命令，要求退出码 0。

```bash
git add android/app/src/main/java/xyz/fenever/assistant/ShellBridge.java android/app/src/main/java/xyz/fenever/assistant/NotificationChannels.java android/app/src/main/java/xyz/fenever/assistant/PrefsIo.java android/app/src/main/java/xyz/fenever/assistant/MainActivity.java android/app/src/main/AndroidManifest.xml
git commit -m "壳的桥：ShellBridge 八方法 + 通知渠道 + PrefsIo 接缝 + MainActivity 装桥"
```

---

### Task 6: 提醒排期与到点通知

**Files:**
- Create: `ReminderScheduler.java`、`ReminderReceiver.java`、`BootReceiver.java`
- Modify: `AndroidManifest.xml`（receiver 注册 + `RECEIVE_BOOT_COMPLETED`）

**Interfaces:**
- Consumes: `ReminderStore.dueAt/advance/list`、`ShellBridge.event(String)`
- Produces: `ReminderScheduler.schedule(Context, Reminder)`、`ReminderScheduler.cancel(Context, String id)`

- [ ] **Step 1: ReminderScheduler**

```java
static void schedule(Context context, Reminder reminder) {
    AlarmManager am = context.getSystemService(AlarmManager.class);
    if (am == null) return;
    am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, reminder.at, pendingFor(reminder.id));
}
```

`pendingFor` 用 `PendingIntent.getBroadcast(context, id.hashCode(), intent, FLAG_IMMUTABLE | FLAG_UPDATE_CURRENT)`，intent 指向 `ReminderReceiver` 并 `putExtra("id", id)`。

- [ ] **Step 2: ReminderReceiver**

`onReceive` 里：取 store → `dueAt(System.currentTimeMillis())` → 逐条发通知 → `store.advance(r, now)`
→ 未来记录重新 `schedule`。**不补发历史轮次**（`advance` 的规则已经保证对齐到下一个未来时刻）。

```java
        ReminderStore store = new ReminderStore(PrefsIo.reminders(context));
        long now = System.currentTimeMillis();
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        for (Reminder r : store.dueAt(now)) {
            if (context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                    != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                continue;                     // 没授权就一条都不发；记录留着，下次仍会试
            }
            Intent open = new Intent(context, MainActivity.class)
                    .putExtra("open_from", "reminder:" + r.id)
                    .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            PendingIntent tap = PendingIntent.getActivity(context, r.id.hashCode(), open,
                    PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
            nm.notify(r.id.hashCode(), new Notification.Builder(context, NotificationChannels.REMINDERS)
                    .setSmallIcon(android.R.drawable.ic_popup_reminder)
                    .setContentTitle(r.title)
                    .setContentText(r.body)
                    .setAutoCancel(true)
                    .setContentIntent(tap)
                    .build());
            store.advance(r, now);
            if (store.byId(r.id) != null) schedule(context, store.byId(r.id));
        }
```

- [ ] **Step 3: 第一次设提醒时问授权**

`ShellBridge.scheduleReminder` 成功落库之后、返回之前，若未授权就发起运行时请求（spec §1：
只在用户第一次设提醒时问一次）。`ShellBridge` 持有一个 `Activity` 引用即可（构造参数加一个
`Activity host`，MainActivity 传 `this`）：

```java
        if (activity.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            activity.requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS},
                    NOTIFICATION_PERMISSION_CODE);
        }
```

`MainActivity` 里 `NOTIFICATION_PERMISSION_CODE = 1003`（1001 文件选择、1002 相机已占用）。
授权结果不需要额外处理：下一次到点时 `ReminderReceiver` 自己会再查一次。

- [ ] **Step 4: BootReceiver**

`intent.getAction().equals(Intent.ACTION_BOOT_COMPLETED)` 时，把 `store.list()` 里所有 `at > now` 的重新 `schedule`。已过点的 `once` 不补发，交给 `advance` 规则。

- [ ] **Step 5: 清单注册**

```xml
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />
```

以及 `<receiver android:name=".ReminderReceiver" android:exported="false" />` 与带 `BOOT_COMPLETED` intent-filter 的 `.BootReceiver`。

- [ ] **Step 6: 型检 + 提交**

> 本任务**故意不调** `AssistantWidget.refresh(...)`——那个类要到 Task 8 才存在，现在引用它型检就红。
> Task 8 的 Step 4 会把刷新调用补回来。

```bash
git add android/app/src/main/java/xyz/fenever/assistant/ReminderScheduler.java android/app/src/main/java/xyz/fenever/assistant/ReminderReceiver.java android/app/src/main/java/xyz/fenever/assistant/BootReceiver.java android/app/src/main/java/xyz/fenever/assistant/ShellBridge.java android/app/src/main/java/xyz/fenever/assistant/MainActivity.java android/app/src/main/AndroidManifest.xml
git commit -m "壳的提醒排期：近似闹钟 + 到点通知 + 首次设提醒时问授权 + 开机重排"
```

---

### Task 7: 系统分享入口

**Files:**
- Create: `ShareActivity.java`
- Modify: `AndroidManifest.xml`（`ACTION_SEND` intent-filter）、`MainActivity.java`（收 `pending_share` extra）

**Interfaces:**
- Consumes: `ShareInbox.put(...)`、`ShellBridge.event(...)`
- Produces: 冷启动 extra `pending_share=<id>`；事件 `{"type":"share","id":"<id>"}`

- [ ] **Step 1: ShareActivity**

`Theme.NoDisplay`、`exported=true`、只处理 `ACTION_SEND` 的 `EXTRA_STREAM` 单条；按 mime 白名单（`text/plain`、`image/*`、`application/pdf`）；用 `getContentResolver().openInputStream(uri)` 交给 `ShareInbox.put(id, in, name, mime, size)`，`name` 从 `DISPLAY_NAME` 列取、取不到用时间戳；然后 `startActivity(MainActivity)` 带 `pending_share`，`finish()`。**不整块读进内存。**

- [ ] **Step 2: MainActivity 转发**

`onCreate` 读 `getIntent().getStringExtra("pending_share")`，存进 `bridge`，等 `onPageFinished` 一起冲。

- [ ] **Step 3: 清单 + 型检 + 提交**

```bash
git add android/app/src/main/java/xyz/fenever/assistant/ShareActivity.java android/app/src/main/java/xyz/fenever/assistant/MainActivity.java android/app/src/main/AndroidManifest.xml
git commit -m "壳的系统分享入口：ACTION_SEND 落到待上传队列，字节经桥分块交给网页上传"
```

---

### Task 8: 桌面组件与快捷入口

**Files:**
- Create: `AssistantWidget.java`、`res/layout/widget_assistant.xml`、`res/xml/widget_assistant_info.xml`、`res/xml/shortcuts.xml`
- Modify: `AndroidManifest.xml`、`ReminderStore` 写入处触发刷新

**Interfaces:**
- Consumes: `ReminderStore.list()`、`activeOwner()`
- Produces: `AssistantWidget.refresh(Context)`

- [ ] **Step 1: 布局（只用平台控件）**

`widget_assistant.xml`：竖向 `LinearLayout` + 标题 `TextView` + 4 个 id 为 `row1..row4` 的 `TextView` + 一行两个按钮 `btnCamera`、`btnChat`。**不用 RecyclerView**（那要 `RemoteViewsService`）。

- [ ] **Step 2: AssistantWidget**

`onUpdate` 与 `refresh(Context)` 都：按 `activeOwner` 取 `list()` 中 `at` 落在今天的前 4 条填进 `row1..row4`，超出显示"还有 N 条"，每行 `setOnClickPendingIntent` 打开 MainActivity 带 `open_from=reminder:<id>`；两个按钮分别带 `open_from=camera` / `open_from=new_chat`。

> 按钮**不直接拉相机**：那需要 `FileProvider`（在 `androidx.core` 里，会破零依赖）。改为打开 App 并告诉网页该弹哪个面板，网页的 `<input type=file capture>` 走已跑通的文件选择 + 相机权限链路。

- [ ] **Step 3: shortcuts.xml 与 meta-data**

静态两条快捷方式（拍照提问、新开对话），指向 `.MainActivity` + 对应 `open_from` extra。

- [ ] **Step 4: 把 Task 6 欠下的刷新调用接回来**

三处写入之后都要刷组件（提醒被增、删、推进时组件上的"今日"才会变）：

```java
        AssistantWidget.refresh(context);
```

分别加在 `ShellBridge.scheduleReminder` 成功之后、`ShellBridge.cancelReminder` 之后、
以及 `ReminderReceiver.onReceive` 的循环结束之后。

- [ ] **Step 5: 型检 + 提交**

```bash
git add android/app/src/main/java/xyz/fenever/assistant/AssistantWidget.java android/app/src/main/res android/app/src/main/AndroidManifest.xml
git commit -m "壳的桌面组件与快捷入口：今日提醒 + 两个跳转，零 androidx"
```

---

### Task 9: 版本、发布与实机验收

- [ ] **Step 1:** `android/app/build.gradle`：`versionCode 13`、`versionName "0.13"`。
- [ ] **Step 2:** 推 develop，等 `android-tests.yml` 绿（JVM 单测 + assembleDebug 真编译）。**绿之前不许打 tag。**
- [ ] **Step 3:** 改 `release-apk.yml` 的 Release 说明：删掉"只是个壳、改服务端不用重装"里已被推翻的部分，加上"壳现在也有功能（提醒/分享/组件），服务端改版仍不需要重装，但壳自身的能力要更新包"。
- [ ] **Step 4:** `git tag v0.13 && git push origin v0.13`（tag 强移会触发工作流，本仓已实测）。
- [ ] **Step 5:** 按 spec §9 的 8 条实机清单在手机上逐条点，**每条要么通过要么如实报失败**，不通过的项写进 Release 说明的"已知问题"。
