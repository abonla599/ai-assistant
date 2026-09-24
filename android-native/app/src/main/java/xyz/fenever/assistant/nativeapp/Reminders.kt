package xyz.fenever.assistant.nativeapp

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

/* 设备侧提醒：整套语义从旧壳（android/app 的 ReminderStore/ReminderScheduler/
 * ReminderReceiver）逐条移植——服务端没有"提醒"这件事，它只活在这台手机上。
 * - at 是 epoch 毫秒，由界面用本机时区算好交进来，这里不做任何日历运算；
 * - once 响过即删；daily/weekly 推进到下一个【未来】时刻，不补发错过的轮次；
 * - 通知权限没给时记一笔 missed 但照样推进重排——否则会变成"列表里看得见、
 *   之后就算授权了也永远不响"的孤儿（v0.18 修过的坑，注释就抄在原处）；
 * - 每人上限 32 条（too_many）。 */

@Serializable
data class ReminderItem(val id: String, val owner: String, var at: Long,
                        val title: String = "", val body: String = "",
                        val repeat: String = "once",
                        var firedAt: Long = 0, var missed: Long = 0)

object ReminderStore {
    private const val MAX_PER_OWNER = 32
    private const val DAY = 24L * 3600_000L
    private const val WEEK = 7L * DAY

    private val json = Json { ignoreUnknownKeys = true }
    private lateinit var file: File
    private val items = mutableListOf<ReminderItem>()
    private var owner: String? = null

    fun init(ctx: Context) {
        file = File(ctx.filesDir, "reminders.json")
        load()
    }

    private fun load() {
        items.clear()
        val raw = runCatching { if (file.exists()) file.readText() else null }.getOrNull()
        if (raw == null || raw.isBlank()) return
        val root = runCatching { json.decodeFromString<StoreDump>(raw) }.getOrNull() ?: return
        owner = root.owner?.ifEmpty { null }
        items.addAll(root.items.filter { it.id.isNotEmpty() && it.owner.isNotEmpty() })
        sort()
    }

    @Serializable private data class StoreDump(val owner: String? = null,
                                               val items: List<ReminderItem> = emptyList())

    private fun flush() {
        runCatching {
            file.writeText(json.encodeToString(StoreDump(owner, items.toList())))
        }
    }

    /** 换人/重进设置时从盘上重读：接收器和界面各拿同一份文件，谁写都是整块覆盖，
     *  不回读会把已删掉的 once 又写回去、同一条响两次。 */
    fun reload() = synchronized(this) { load() }

    @Synchronized fun setOwner(user: String) {
        val next = user.ifEmpty { null }
        if (next == owner) return
        owner = next
        flush()
    }

    @Synchronized fun activeOwner(): String? = owner

    /** 添加：owner 取当前身份；超过 32 条回 false（界面那句 too_many 文案照抄壳）。 */
    @Synchronized fun add(at: Long, title: String, body: String, repeat: String): ReminderItem? {
        val own = owner ?: return null
        if (list().size >= MAX_PER_OWNER) return null
        val r = ReminderItem(id = java.util.UUID.randomUUID().toString().replace("-", "").take(12),
            owner = own, at = at, title = title, body = body, repeat = repeat)
        items.add(r)
        sort()
        flush()
        return r
    }

    @Synchronized fun cancel(id: String): Boolean {
        val removed = items.removeAll { it.id == id }
        if (removed) flush()
        return removed
    }

    /** 只回当前 owner 的；没 setOwner 时空表（fail-closed，同壳）。 */
    @Synchronized fun list(): List<ReminderItem> {
        val own = owner ?: return emptyList()
        return items.filter { it.owner == own }
    }

    @Synchronized fun dueAt(now: Long): List<ReminderItem> = list().filter { it.at <= now }

    @Synchronized fun byId(id: String): ReminderItem? = items.firstOrNull { it.id == id }

    @Synchronized fun markFired(r: ReminderItem?, at: Long) {
        if (r == null) return
        r.firedAt = at
        flush()
    }

    @Synchronized fun markMissed(r: ReminderItem?) {
        if (r == null) return
        r.missed += 1
        flush()
    }

    /** once 删除；daily/weekly 推到下一个【未来】时刻。 */
    @Synchronized fun advance(r: ReminderItem?, now: Long) {
        if (r == null) return
        if (r.repeat == "daily" || r.repeat == "weekly") {
            val step = if (r.repeat == "daily") DAY else WEEK
            var next = r.at
            while (next <= now) next += step
            r.at = next
            sort()
        } else {
            items.remove(r)
        }
        flush()
    }

    private fun sort() { items.sortBy { it.at } }
}

object ReminderScheduler {
    /** 档位判定与旧壳 AlarmPolicy 逐字一致：31 以下不问也不需要问。 */
    fun exactAllowed(ctx: Context): Boolean {
        val manager = ctx.getSystemService(AlarmManager::class.java) ?: return false
        return Build.VERSION.SDK_INT < 31 || manager.canScheduleExactAlarms()
    }

    fun schedule(ctx: Context, r: ReminderItem) {
        val manager = ctx.getSystemService(AlarmManager::class.java) ?: return
        val pending = pendingFor(ctx, r.id)
        val exact = Build.VERSION.SDK_INT < 31 || manager.canScheduleExactAlarms()
        if (exact) manager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, r.at, pending)
        else manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, r.at, pending)
    }

    fun cancel(ctx: Context, id: String) {
        val manager = ctx.getSystemService(AlarmManager::class.java) ?: return
        manager.cancel(pendingFor(ctx, id))
    }

    private fun pendingFor(ctx: Context, id: String): PendingIntent {
        val intent = Intent(ctx, ReminderReceiver::class.java).putExtra("id", id)
        return PendingIntent.getBroadcast(ctx, id.hashCode(), intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }
}

object ReminderChannels {
    const val ID = "ai_reminders"

    fun ensure(ctx: Context) {
        if (Build.VERSION.SDK_INT < 26) return
        val manager = ctx.getSystemService(NotificationManager::class.java) ?: return
        if (manager.getNotificationChannel(ID) == null) {
            manager.createNotificationChannel(NotificationChannel(
                ID, "提醒", NotificationManager.IMPORTANCE_HIGH))
        }
    }

    fun notificationsGranted(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            androidx.core.content.ContextCompat.checkSelfPermission(ctx,
                android.Manifest.permission.POST_NOTIFICATIONS) ==
                android.content.pm.PackageManager.PERMISSION_GRANTED
}

class ReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        ReminderStore.init(context)
        ReminderChannels.ensure(context)
        val now = System.currentTimeMillis()
        val due = ReminderStore.dueAt(now)
        if (due.isEmpty()) return

        val granted = ReminderChannels.notificationsGranted(context)
        for (r in due) {
            if (granted) {
                notify(manager, context, r)
                ReminderStore.markFired(r, now)   // 先记账再推进：advance 对 once 就是删除
            } else {
                ReminderStore.markMissed(r)       // 没响也推进重排，绝不留孤儿轮次
            }
            ReminderStore.advance(r, now)
            val next = ReminderStore.byId(r.id)   // once 已被 advance 删掉 → null
            if (next != null) ReminderScheduler.schedule(context, next)
        }
    }

    private fun notify(manager: NotificationManager, ctx: Context, r: ReminderItem) {
        val open = Intent(ctx, MainActivity::class.java)
            .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val tap = PendingIntent.getActivity(ctx, r.id.hashCode(), open,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val builder = androidx.core.app.NotificationCompat.Builder(ctx, ReminderChannels.ID)
        manager.notify(r.id.hashCode(),
            builder.setSmallIcon(android.R.drawable.ic_popup_reminder)
                .setContentTitle(r.title)
                .setContentText(r.body)
                .setAutoCancel(true)
                .setContentIntent(tap)
                .build())
    }
}

/** 开机重排：AlarmManager 的闹钟不跨重启，把未来时刻的那批重新排上。 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        ReminderStore.init(context)
        val now = System.currentTimeMillis()
        ReminderStore.list().filter { it.at > now }.forEach {
            ReminderScheduler.schedule(context, it)
        }
    }
}
