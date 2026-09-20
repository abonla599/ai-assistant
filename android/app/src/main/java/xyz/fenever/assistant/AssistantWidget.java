package xyz.fenever.assistant;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.view.View;
import android.widget.RemoteViews;
import java.util.List;
import java.util.TimeZone;
import xyz.fenever.assistant.core.ReminderStore;
import xyz.fenever.assistant.core.ShellEvents;
import xyz.fenever.assistant.core.WidgetBoard;

/**
 * 桌面组件（spec §5）：今日提醒最多四行 + "还有 N 条" + 三个快捷入口。
 *
 * <p>内容规则全在 {@link WidgetBoard}（纯 Java，有 JVM 单测）：哪天算今天、取几条、
 * 每行写什么、没有归属时说什么。这个类只剩 RemoteViews 的填表动作。
 *
 * <p>只用平台控件、不引 {@code RemoteViewsService}：布局里那四行 TextView 是写死的，
 * 所以既没有 RecyclerView（在 androidx 里）也没有集合项 Provider。
 *
 * <p>两个按钮【不】直接拉相机：那需要一个可写的 content URI，也就是 FileProvider，
 * 而 FileProvider 在 androidx.core 里 —— 会打破零依赖。改成"打开 App 并带上
 * {@code open_from=camera|new_chat}"，由网页决定弹哪个面板（代价是多一步）。
 */
public class AssistantWidget extends AppWidgetProvider {

    /** 三个按钮各占一个 requestCode：与提醒行的 id 哈希分得开，也不会互相顶掉。 */
    private static final int REQUEST_CAMERA = 9101;
    private static final int REQUEST_NEW_CHAT = 9102;
    private static final int REQUEST_CHECK_UPDATE = 9103;
    private static final int[] ROW_VIEWS = {R.id.row1, R.id.row2, R.id.row3, R.id.row4};

    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] appWidgetIds) {
        WidgetBoard board = board(context);
        for (int appWidgetId : appWidgetIds) render(context, manager, appWidgetId, board);
    }

    /**
     * 跨天与换时区要换内容，而 {@code AppWidgetProvider} 的默认实现【不认识】这几个广播：
     * 它的 onReceive 只把 ACTION_APPWIDGET_* 转成回调，其余动作直接丢掉。
     * 所以清单里声明 ACTION_DATE_CHANGED / TIME_CHANGED / TIMEZONE_CHANGED 只解决了投递，
     * 接住它必须在这里自己写——不然早上八点之后组件上还挂着昨天的那一列。
     *
     * <p>空意图在调 super【之前】就挡掉：父类实现的第一句就是 {@code intent.getAction()}，
     * 传 null 进去是它抛 NPE，我们这一侧的 {@code action == null} 判断根本轮不到。
     * 空意图也没有任何动作可转，直接返回不影响 APPWIDGET_UPDATE 那条正常链路。
     */
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) return;
        super.onReceive(context, intent);              // ACTION_APPWIDGET_UPDATE 由它转成 onUpdate
        String action = intent.getAction();
        if (Intent.ACTION_DATE_CHANGED.equals(action)
                || Intent.ACTION_TIME_CHANGED.equals(action)
                || Intent.ACTION_TIMEZONE_CHANGED.equals(action)) {
            refresh(context);
        }
    }

    /**
     * 外部（增/删/到点推进/换 owner）写完表之后叫一句：把桌面上每一份这个组件重画一次。
     *
     * <p>吞掉异常是有意的：这个调用挂在 {@code ShellBridge.scheduleReminder} 与
     * {@code ReminderReceiver} 的收尾上，组件刷新失败不该把"提醒本身排上了/响了"一起拖没。
     * 最坏结果是桌面上那几行旧数据难看一会。
     */
    public static void refresh(Context context) {
        if (context == null) return;
        try {
            AppWidgetManager manager = AppWidgetManager.getInstance(context);
            if (manager == null) return;
            int[] ids = manager.getAppWidgetIds(new ComponentName(context, AssistantWidget.class));
            if (ids == null || ids.length == 0) return;         // 桌面上还没摆这个组件
            WidgetBoard board = board(context);
            for (int id : ids) render(context, manager, id, board);
        } catch (RuntimeException ignored) {
            // 见上面那句话
        }
    }

    private static WidgetBoard board(Context context) {
        ReminderStore store = new ReminderStore(PrefsIo.reminders(context));
        return WidgetBoard.build(store.list(), store.activeOwner(),
                System.currentTimeMillis(), TimeZone.getDefault());
    }

    private static void render(Context context, AppWidgetManager manager,
                               int appWidgetId, WidgetBoard board) {
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_assistant);
        views.setTextViewText(R.id.header, board.header);

        List<WidgetBoard.Row> rows = board.rows;
        for (int i = 0; i < ROW_VIEWS.length; i++) {
            if (i < rows.size()) {
                WidgetBoard.Row row = rows.get(i);
                views.setViewVisibility(ROW_VIEWS[i], View.VISIBLE);
                views.setTextViewText(ROW_VIEWS[i], row.text);
                // 点一行 = 打开助手并带上 reminder:<id>，走的还是通知点进去那条链路。
                // data 里带上 id：万一两条提醒的 hashCode 撞了，requestCode 相同而 data 不同，
                // 系统仍会把它们当成两个待决意图（撞的是同一个才真的会互相顶掉）。
                views.setOnClickPendingIntent(ROW_VIEWS[i], openMain(context,
                        row.id.hashCode(), ShellEvents.OPEN_FROM_REMINDER + row.id,
                        "reminder/" + row.id));
            } else {
                views.setViewVisibility(ROW_VIEWS[i], View.GONE);
            }
        }

        if (board.hidden > 0) {
            views.setViewVisibility(R.id.more, View.VISIBLE);
            views.setTextViewText(R.id.more, "还有 " + board.hidden + " 条");
        } else {
            views.setViewVisibility(R.id.more, View.GONE);
        }

        views.setOnClickPendingIntent(R.id.btnCamera,
                openMain(context, REQUEST_CAMERA, ShellEvents.OPEN_CAMERA, "camera"));
        views.setOnClickPendingIntent(R.id.btnChat,
                openMain(context, REQUEST_NEW_CHAT, ShellEvents.OPEN_NEW_CHAT, "new_chat"));
        views.setOnClickPendingIntent(R.id.btnUpdate,
                openMain(context, REQUEST_CHECK_UPDATE, ShellEvents.OPEN_CHECK_UPDATE,
                        "check_update"));
        manager.updateAppWidget(appWidgetId, views);
    }

    /**
     * 组件上的每个可点处都要一个自己的 PendingIntent。
     *
     * <p>data 那一段不是装饰：PendingIntent 判"是不是同一个"用的是 {@code Intent.filterEquals}，
     * 它比的是 action / data / type / class / categories，【不比 extras】。所以只换 open_from
     * 而不换 requestCode 或 data 的话，两颗按钮会被系统判成同一个待决意图，
     * 点哪颗都只弹同一个面板。
     */
    private static PendingIntent openMain(Context context, int requestCode,
                                          String openFrom, String slot) {
        Intent open = new Intent(context, MainActivity.class)
                .putExtra("open_from", openFrom)
                .setData(Uri.parse("assistant-widget://" + slot))
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                        | Intent.FLAG_ACTIVITY_CLEAR_TOP
                        | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        return PendingIntent.getActivity(context, requestCode, open,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }
}
