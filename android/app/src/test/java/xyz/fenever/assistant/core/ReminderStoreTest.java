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

    /**
     * 壳里同时存在两个写者：网页这边的 store（MainActivity 持有）与到点通知那边的
     * ReminderReceiver（每次广播 new 一个）。谁写都是整块覆盖，所以「重读」是唯一能同步的手段。
     * 这两条测试钉的就是 Task 6 里 ShellBridge.syncReminders() 依赖的行为。
     */
    @Test public void reloadPicksUpWhatAnotherInstanceAlreadyWrote() {
        MemIo io = new MemIo();
        ReminderStore page = new ReminderStore(io);            // 网页这边
        page.setOwner("alice");
        page.add(r("r-aaaaaaaaaaaa", "alice", 1000L, "once"));
        page.add(r("r-bbbbbbbbbbbb", "alice", 2000L, "once"));

        ReminderStore receiver = new ReminderStore(io);        // 接收器那边：读表、推进、落盘
        receiver.setOwner("alice");
        for (Reminder due : receiver.dueAt(1500L)) receiver.advance(due, 1500L);

        assertEquals("接收器那边已经少了一条", 1, receiver.list().size());
        assertEquals("没重读的这边还留着旧的", 2, page.list().size());
        page.reload();
        assertEquals(1, page.list().size());
        assertEquals("r-bbbbbbbbbbbb", page.list().get(0).id);
    }

    /** reload 不能把归属读丢：未 setOwner 时 fail-closed 这条得顶得住重读。 */
    @Test public void reloadKeepsTheFailClosedOwner() {
        MemIo io = new MemIo();
        ReminderStore store = new ReminderStore(io);
        store.setOwner("");                                    // 清空归属
        store.add(r("r-aaaaaaaaaaaa", "alice", 1000L, "once"));
        store.reload();
        assertNull(store.activeOwner());
        assertTrue(store.list().isEmpty());
    }
}
