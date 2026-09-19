package xyz.fenever.assistant;

import android.app.Activity;
import android.content.ContentResolver;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Bundle;
import android.provider.OpenableColumns;
import android.widget.Toast;
import java.io.File;
import java.io.InputStream;
import xyz.fenever.assistant.core.ShareInbox;
import xyz.fenever.assistant.core.SharePolicy;

/**
 * 系统分享入口（spec §4）：相册/文件里点「分享 → AI 助手」时系统拉起的跳板。
 *
 * <p>它自己【不出界面】（清单里是 {@code Theme.NoDisplay}），做完三件事就 finish：
 * 把字节抄进 {@code cacheDir/shares/<新 id>} → 启动 {@link MainActivity} 并带上
 * {@code pending_share=<id>} → 交给网页分块读走。
 *
 * <p>为什么字节不直接递给网页：桥只传 id 不传内容（spec §2 铁律①）。分享来的文件名是
 * 外部可控字符串，一旦拼进 {@code evaluateJavascript} 就是一段 JS 注入。
 *
 * <p>三条硬约束落在哪：
 * <ul>
 *   <li>只接 {@code ACTION_SEND} 的【单条】 EXTRA_STREAM —— {@code SEND_MULTIPLE} 连清单都没声明，
 *       这里再判一次 action 兜住绕进来的调用；</li>
 *   <li>mime 白名单与 10MB 闸门在 {@link SharePolicy}（那部分有 JVM 单测）；</li>
 *   <li>拷贝走 {@link ShareInbox#put} 的 8KB 缓冲【流式】写：不把整块读进内存，
 *       失败也不留半成品文件。</li>
 * </ul>
 *
 * <p>取舍如实记录：拷贝做在主线程（上限 10MB）。不能挪到后台线程——分享方给的那份 URI
 * 读权限是跟着【这个 activity 的生命周期】发的，finish 之后就没了，后台线程再读只会拿到
 * SecurityException。代价是一个特别慢的 Provider 理论上能把这里卡住。
 */
public class ShareActivity extends Activity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // 每条退出路径都必须 finish()：Theme.NoDisplay 的 activity 只要带着界面活过一次
        // onResume，系统就直接抛 "Activities that use Theme.NoDisplay must call finish()"。
        Intent intent = getIntent();
        if (intent == null || !Intent.ACTION_SEND.equals(intent.getAction())) {
            finish();
            return;
        }
        intake(intent);
    }

    @SuppressWarnings("deprecation")
    private void intake(Intent intent) {
        String mime = intent.getType();
        Uri stream;
        try {
            // 用一参数那个：两参数的 getParcelableExtra(String, Class) 是 API 33 才加的，
            // 在 23-32 上不存在——型检绿、真机 NoSuchMethodError 正是这批改动反复踩的坑。
            stream = intent.getParcelableExtra(Intent.EXTRA_STREAM);
        } catch (RuntimeException notAUri) {
            stream = null;                    // 分享方塞了个不是 Uri 的 Parcelable
        }

        Meta meta = stream == null ? new Meta() : readMeta(stream);
        SharePolicy.Intake decision = SharePolicy.accept(mime, meta.size, stream != null);
        if (decision != SharePolicy.Intake.OK) {
            refuse(decision);
            return;
        }

        String id = SharePolicy.newId();
        String name = SharePolicy.displayName(meta.name, mime, System.currentTimeMillis());
        // 每次新建一个 ShareInbox：它与 MainActivity 那份共用同一块 SharedPreferences
        // （进程内是同一份内存缓存），谁写都是整块覆盖，所以不需要跨实例同步。
        ShareInbox inbox = new ShareInbox(new File(getCacheDir(), "shares"), PrefsIo.shares(this));
        InputStream in = null;
        boolean stored;
        try {
            in = getContentResolver().openInputStream(stream);
            stored = inbox.put(id, in, name, mime, meta.size);
        } catch (Exception readFailed) {
            // Provider 挂了、权限没给上、流读一半断了：put 里那道实际字节闸门会删掉半成品
            stored = false;
        } finally {
            closeQuietly(in);
        }
        if (!stored) {
            // 走到这里基本只剩两种原因：实际字节超过声明值所暗示的 10MB，或目录建不出来
            toast("没能收下这个文件，请再分享一次");
            finish();
            return;
        }
        openMain(id);
    }

    /** 带着 id 去叫主界面，然后收掉自己这个不出界面的跳板。 */
    private void openMain(String id) {
        Intent open = new Intent(this, MainActivity.class).putExtra("pending_share", id);
        // NEW_TASK：从别的 App 的任务栈里进来，没有它 startActivity 直接抛；
        // CLEAR_TOP + SINGLE_TOP：助手已经开着时复用那个 WebView——重建它等于把正聊到
        // 一半的对话、正在流式输出的回答整个丢掉，extras 改走 MainActivity.onNewIntent。
        open.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                | Intent.FLAG_ACTIVITY_CLEAR_TOP
                | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        try {
            startActivity(open);
        } catch (Exception cannotOpen) {
            toast("文件已经收下了，在助手里打开对话附件区就能看到");   // 队列里的件 30 分钟内不丢
        }
        finish();
    }

    private void refuse(SharePolicy.Intake reason) {
        toast(message(reason));
        finish();
    }

    /**
     * 用 Toast 而不是弹框：这个 activity 按硬约束不出界面。
     *
     * <p>残余风险如实写在这里：Android 12 起系统会掐掉【后台应用】的文字 Toast，而
     * NoDisplay 的 activity 全程没有窗口——这条提示在 12+ 上有可能根本不显示。
     * 真机验收清单 spec §9 第 6 条（分享 >10MB 要有明确拒绝提示）就是冲着一句"应该能显示"
     * 来的：在那台机器上点一次才知道成不成立，不成立就要把拒绝改成一个真正有界面的
     * activity（或让 MainActivity 带一个固定枚举的提示 extra 过去，但那要网页侧配合）。
     */
    private void toast(String text) {
        Toast.makeText(this, text, Toast.LENGTH_SHORT).show();
    }

    /** 拒绝一定要有话说（spec §9 第 6 条：分享 >10MB 要"有明确拒绝提示，不是静默没反应"）。 */
    private static String message(SharePolicy.Intake reason) {
        switch (reason) {
            case TOO_LARGE:
                return "太大了，助手的单个附件上限是 "
                        + (ShareInbox.MAX_BYTES / (1024L * 1024L)) + "MB";
            case NO_STREAM:
                return "这条分享里只有文字、没有文件；要发文字请直接粘贴到输入框";
            case UNSUPPORTED_MIME:
            default:
                return "助手收文字、图片和 PDF，这一种格式收不下";
        }
    }

    /** 一次查询把展示名与声明体积都拿回来；查不到就名字留空（交给 SharePolicy 兜）、体积未知。 */
    private Meta readMeta(Uri uri) {
        Meta meta = new Meta();
        ContentResolver resolver = getContentResolver();
        Cursor cursor = null;
        try {
            cursor = resolver.query(uri,
                    new String[]{OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE},
                    null, null, null);
            if (cursor != null && cursor.moveToFirst()) {
                int nameColumn = columnIndex(cursor, OpenableColumns.DISPLAY_NAME);
                int sizeColumn = columnIndex(cursor, OpenableColumns.SIZE);
                if (nameColumn >= 0 && !cursor.isNull(nameColumn)) {
                    meta.name = cursor.getString(nameColumn);
                }
                if (sizeColumn >= 0 && !cursor.isNull(sizeColumn)) {
                    meta.size = cursor.getLong(sizeColumn);
                }
            }
        } catch (Exception ignored) {
            // 个别 Provider 不让 query（或压根没有这两列）：名字回退成时间戳名，
            // 体积留"未知"，真正的上限仍由 ShareInbox 按实际字节数把。
        } finally {
            if (cursor != null) cursor.close();
        }
        return meta;
    }

    private static int columnIndex(Cursor cursor, String column) {
        try {
            return cursor.getColumnIndex(column);
        } catch (Exception ignored) {
            return -1;
        }
    }

    private static void closeQuietly(InputStream in) {
        if (in == null) return;
        try {
            in.close();
        } catch (Exception ignored) {
            // 读完就丢：关不上也不影响已经落盘的那份
        }
    }

    private static final class Meta {
        String name;
        long size = SharePolicy.UNKNOWN_SIZE;
    }
}
