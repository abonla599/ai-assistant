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
 * 系统分享入口（spec §4）：相册/文件/别的 App 里点「分享 → AI 助手」时系统拉起的跳板。
 *
 * <p>它自己【不出界面】（清单里是 {@code Theme.NoDisplay}），做完三件事就 finish：
 * 把字节抄进 {@code cacheDir/shares/<新 id>} → 启动 {@link MainActivity} 并带上
 * {@code pending_share=<id>} → 交给网页分块读走。
 *
 * <p>两条入口，落进同一个队列、同一套 id 与清理规则：
 * <ul>
 *   <li>{@code EXTRA_STREAM}（图片/PDF/文件）→ 流式复制，10MB 闸门；</li>
 *   <li>只有 {@code EXTRA_TEXT}（分享一段文字时几乎都只给这一条）→ 落成 UTF-8 小文件，
 *       1MB 闸门（{@code backend/app/core/uploads.py:26} 的文本上限）。
 *       v0.13 只认上一条，于是清单里声明的 {@code text/plain} 基本白声明。</li>
 * </ul>
 *
 * <p>为什么字节不直接递给网页：桥只传 id 不传内容（spec §2 铁律①）。分享来的文件名是
 * 外部可控字符串，一旦拼进 {@code evaluateJavascript} 就是一段 JS 注入。
 *
 * <p>三条硬约束落在哪：
 * <ul>
 *   <li>只接 {@code ACTION_SEND} 的【单条】 EXTRA_STREAM —— {@code SEND_MULTIPLE} 连清单都没声明，
 *       这里再判一次 action 兜住绕进来的调用；</li>
 *   <li>mime 白名单与体积闸门在 {@link SharePolicy}（那部分有 JVM 单测）；</li>
 *   <li>拷贝走 {@link ShareInbox#put} 的 8KB 缓冲【流式】写：不把整块读进内存，
 *       失败也不留半成品文件。</li>
 * </ul>
 *
 * <p>取舍如实记录：拷贝做在主线程（上限 10MB）。不能挪到后台线程——分享方给的那份 URI
 * 读权限是跟着【这个 activity 的生命周期】发的，finish 之后就没了，后台线程再读只会拿到
 * SecurityException。代价是一个特别慢的 Provider 理论上能把这里卡住。
 */
public class ShareActivity extends Activity {

    /** 交给网页的 extra 名：值只会是 {@link SharePolicy#code} 那五个固定码之一。 */
    static final String EXTRA_SHARE_REFUSED = "share_refused";

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
        if (stream != null) {
            intakeStream(mime, stream);
            return;
        }
        intakeText(mime, textOf(intent));
    }

    /** 文件那一路：查列拿声明体积 → 流式复制进队列 → 带 id 去叫主界面。 */
    private void intakeStream(String mime, Uri stream) {
        Meta meta = readMeta(stream);
        SharePolicy.Intake decision = SharePolicy.accept(mime, meta.size);
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
            // 走到这里只剩两种原因：实际字节超过声明值所暗示的 10MB，或目录建不出来
            refuse(SharePolicy.Intake.READ_FAILED);
            return;
        }
        if (!openMainWith("pending_share", id)) {
            toast("文件已经收下了，在助手里打开对话附件区就能看到");   // 队列里的件 30 分钟内不丢
        }
        finish();
    }

    /**
     * 纯文本那一路：{@code EXTRA_TEXT} 转成 UTF-8 字节后交给同一个队列。
     *
     * <p>不给它一条单独的"文本直达网页"通道是刻意的：走 {@link ShareInbox#putText} 之后，
     * id 白名单、30 分钟 TTL、孤儿认领、consume 即删、512KB 分块这些规则对文字与图片
     * 完全同一套，网页也一套代码（拿到 text/plain 的 Blob 走现有 {@code POST /v1/uploads}）。
     */
    private void intakeText(String mime, String text) {
        SharePolicy.Intake decision = SharePolicy.acceptText(mime, text);
        if (decision != SharePolicy.Intake.OK) {
            refuse(decision);
            return;
        }
        String id = SharePolicy.newId();
        String name = SharePolicy.displayName(null, ShareInbox.TEXT_MIME, System.currentTimeMillis());
        ShareInbox inbox = new ShareInbox(new File(getCacheDir(), "shares"), PrefsIo.shares(this));
        if (!inbox.putText(id, text, name)) {
            refuse(SharePolicy.Intake.READ_FAILED);
            return;
        }
        if (!openMainWith("pending_share", id)) {
            toast("文字已经收下了，在助手里打开对话附件区就能看到");
        }
        finish();
    }

    /**
     * {@code EXTRA_TEXT} 可能是 {@code SpannedString} 而不是 {@code String}：
     * {@code getStringExtra} 对前者直接回 null（等于把能收的分享判成没内容），
     * 所以按 CharSequence 取再转字符串。
     */
    private String textOf(Intent intent) {
        try {
            CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
            return text == null ? null : text.toString();
        } catch (RuntimeException weird) {
            return null;                        // 分享方给了个读不动的 Parcelable
        }
    }

    /**
     * 带着一条 extra 去叫主界面，然后收掉自己这个不出界面的跳板。
     *
     * @return false 表示根本没叫开——这时网页那侧的提示也送不到，只剩 Toast 那一条路
     */
    private boolean openMainWith(String key, String value) {
        Intent open = new Intent(this, MainActivity.class).putExtra(key, value);
        // NEW_TASK：从别的 App 的任务栈里进来，没有它 startActivity 直接抛；
        // CLEAR_TOP + SINGLE_TOP：助手已经开着时复用那个 WebView——重建它等于把正聊到
        // 一半的对话、正在流式输出的回答整个丢掉，extras 改走 MainActivity.onNewIntent。
        open.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                | Intent.FLAG_ACTIVITY_CLEAR_TOP
                | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        try {
            startActivity(open);
            return true;
        } catch (Exception cannotOpen) {
            return false;
        }
    }

    /**
     * 拒绝走【两条互不依赖】的路：Toast 即时一句，同时把固定码交给网页画。
     *
     * <p>为什么要补第二条：Android 12 起重绘了 Toast，且官方文档写明文字 Toast 只在
     * 【应用处于前台】时显示——而这个 activity 按硬约束全程没有窗口，正落在"可能被掐"的那一侧。
     * 本机没有实机可证它到底显不显示，所以两条都留着：12+ 上 Toast 若被掐，网页那条还在；
     * 网页还没接这个事件类型时（当前 {@code onShellEvent} 忽略未知 type），11 及以下的
     * Toast 还在。剩下的那一半只能实机验（spec §9 第 6 条）。
     */
    private void refuse(SharePolicy.Intake reason) {
        toast(message(reason));
        String code = SharePolicy.code(reason);
        if (code != null) openMainWith(EXTRA_SHARE_REFUSED, code);
        finish();
    }

    private void toast(String text) {
        Toast.makeText(this, text, Toast.LENGTH_SHORT).show();
    }

    /** 拒绝一定要有话说（spec §9 第 6 条：分享 >10MB 要"有明确拒绝提示，不是静默没反应"）。 */
    private static String message(SharePolicy.Intake reason) {
        switch (reason) {
            case TOO_LARGE:
                return "太大了，助手的单个附件上限是 "
                        + (ShareInbox.MAX_BYTES / (1024L * 1024L)) + "MB";
            case TEXT_TOO_LARGE:
                return "这段文字超过 " + (ShareInbox.MAX_TEXT_BYTES / (1024L * 1024L))
                        + "MB，助手收不下；请截短后再分享";
            case READ_FAILED:
                return "没能收下这次分享的内容，请再分享一次";
            case NO_STREAM:
                return "这条分享里既没有文件也没有文字；要发文字请直接粘贴到输入框";
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
