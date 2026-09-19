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
