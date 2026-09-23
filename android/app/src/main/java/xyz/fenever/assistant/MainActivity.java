package xyz.fenever.assistant;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.ValueCallback;
import android.widget.Toast;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLDecoder;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import xyz.fenever.assistant.core.ReleasePlan;
import xyz.fenever.assistant.core.ReminderStore;
import xyz.fenever.assistant.core.ShellEvents;
import xyz.fenever.assistant.core.ShareInbox;

/**
 * AI 助手的 WebView 外壳。
 *
 * 界面与后端全部在线上（https://ai.fenever.xyz/app/），壳只负责几件网页自己做不到的事：
 * 给 WebView 提供原生文件选择、把网页的摄像头请求转成运行时权限、把带 attachment 的响应
 * 交给系统 DownloadManager 落到公共下载目录、以及断网时给出可读提示。
 * 这些全走 Android 平台 API，仍然不引任何第三方依赖；因此改界面不需要重新打包 APK。
 */
public class MainActivity extends Activity {

    private static final int FILE_CHOOSER_CODE = 1001;
    private static final int CAMERA_PERMISSION_CODE = 1002;

    // 从 contentDisposition 里抠文件名用；同时兼容 filename= 与 RFC 5987 的 filename*=
    private static final Pattern DISPOSITION_FILENAME =
            Pattern.compile("filename\\*?\\s*=\\s*(?:\"([^\"]*)\"|([^;]+))", Pattern.CASE_INSENSITIVE);

    /** 安装包的 MIME：下载请求与安装 Intent 两边都用它，别写两遍字符串。 */
    private static final String APK_MIME = "application/vnd.android.package-archive";

    private WebView webview;
    private ValueCallback<Uri[]> filePathCallback;
    private PermissionRequest pendingCameraRequest;

    // 网页与原生之间唯一的口子，以及它背后的两张表（都从 SharedPreferences 恢复）
    private ShellBridge bridge;
    private ReminderStore reminderStore;
    private ShareInbox shareInbox;

    // DownloadManager 的 enqueue id -> 展示用的文件名；回调线程与接收器都在主线程，普通 HashMap 够用
    private final Map<Long, String> pendingDownloads = new HashMap<>();

    /**
     * 「检查更新」这一条自己占的三格状态。
     *
     * <p>安装包的下载 id 必须与网页附件那张表【分开】：混在一起的话，一次 APK 下完会去弹
     * "已保存到「下载」"，而真正该做的（起系统安装页）没人做——正好是"效果没了但不报错"。
     */
    private boolean updateCheckRunning;
    private long pendingApkId = -1L;
    private String pendingApkVersion;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        webview = new WebView(this);
        setContentView(webview);
        // 避免冷启动时先闪一下白屏
        webview.setBackgroundColor(Color.parseColor("#0e1013"));

        WebSettings settings = webview.getSettings();
        settings.setJavaScriptEnabled(true);
        // 口令、会话 id、角色设定都存在 localStorage；不开这个等于每次进来都要重填
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        // 只走 HTTPS，不放行混合内容
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);

        // 原生能力（提醒/分享入口）唯一的入口：一个对象，方法清单见 ShellBridge 的类注释。
        // 通知渠道要先建好——第一次设提醒时发的那条通知没有渠道会在 Android 8+ 上直接发不出去。
        NotificationChannels.ensure(this);
        reminderStore = new ReminderStore(PrefsIo.reminders(this));
        // 分享件走 cacheDir：系统随时可能清掉它，所以 ShareInbox 每次读都先看文件还在不在
        shareInbox = new ShareInbox(new File(getCacheDir(), "shares"), PrefsIo.shares(this));
        bridge = new ShellBridge(this, webview, reminderStore, shareInbox);
        webview.addJavascriptInterface(bridge, "AssistantShell");
        // 冷启动带进来的 extra（点通知、从分享面板进来）先攒着，等页面加载完再发给网页
        bridge.queueStartupEvent(getIntent());
        // 长按图标「检查更新」那条快捷方式走的也是冷启动，但它整条都活在原生侧，见 maybeCheckUpdate
        maybeCheckUpdate(getIntent());

        webview.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                // 开始跳转就把主线程上的「就绪」标志清掉，别让排着的事件发给正在换掉的页面
                bridge.notePageStarted(url);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                // 页面没加载完就 evaluateJavascript 会静默丢失：通知点了没反应就是这么来的
                bridge.notePageFinished(url);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                // 只接管主文档失败，否则页面里的小资源报错会把界面整个换掉
                if (request.isForMainFrame()) {
                    view.loadDataWithBaseURL(null, offlineHtml(), "text/html", "utf-8", null);
                }
            }

            @Override
            public void onReceivedSslError(WebView view,
                                           android.webkit.SslErrorHandler handler,
                                           android.net.http.SslError error) {
                // 绝不 proceed：证书有问题时宁可连不上
                handler.cancel();
            }
        });

        webview.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                }
                filePathCallback = callback;

                Intent pick = new Intent(Intent.ACTION_GET_CONTENT);
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                pick.setType(acceptIsImageOnly(params.getAcceptTypes()) ? "image/*" : "*/*");
                if (params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) {
                    pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                }
                try {
                    startActivityForResult(pick, FILE_CHOOSER_CODE);
                } catch (Exception e) {
                    filePathCallback = null;
                    return false;
                }
                return true;
            }

            @Override
            public void onPermissionRequest(final PermissionRequest request) {
                boolean wantsCamera = false;
                for (String res : request.getResources()) {
                    if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(res)) {
                        wantsCamera = true;
                    }
                }
                if (wantsCamera && checkSelfPermission(android.Manifest.permission.CAMERA)
                        != PackageManager.PERMISSION_GRANTED) {
                    // WebView 不会自己弹系统授权，必须先由宿主拿到运行时权限
                    pendingCameraRequest = request;
                    requestPermissions(new String[]{android.Manifest.permission.CAMERA},
                            CAMERA_PERMISSION_CODE);
                    return;
                }
                grantAllowedResources(request);
            }

            /**
             * 只把壳真正桥接过的资源类型（摄像头）交回 grant，其余一律 deny。
             *
             * 原来两处都是 request.grant(request.getResources())：resources 是网页说
             * 要什么就有什么，直接透传等于**由被授权方决定授权清单**——页面今天加一句
             * RESOURCE_AUDIO_CAPTURE（麦克风，清单里连 RECORD_AUDIO 都没声明，用户从未
             * 在任何地方同意过），明天加一个我们还不知道的新类型，壳都会替它批。
             */
            private void grantAllowedResources(PermissionRequest request) {
                String[] allowed = allowedResources(request);
                runOnUiThread(() -> {
                    if (allowed.length > 0) {
                        request.grant(allowed);
                    } else {
                        request.deny();
                    }
                });
            }
        });

        webview.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) ->
                startDownload(url, userAgent, contentDisposition));

        // Android 14 起，Context.registerReceiver 只有当过滤器【全部】命中 AOSP
        // IntentFilter.SYSTEM_ONLY_ACTIONS 那张硬编码表时才能省掉导出标志，而
        // DOWNLOAD_COMPLETE 不在表里。v0.13 就是被这一句崩在 onCreate 里、loadUrl 之前：
        // Android 14+ 的设备点图标闪一下回桌面，服务端一条请求都收不到。
        // 带标志的重载是 API 33 才有的，minSdk 23 不能无条件调，所以按版本分岔——
        // 会强制要标志的设备必然 >= 34，这个分岔不会漏。
        // 选 NOT_EXPORTED 而不是 EXPORTED：这条广播只该由系统的 DownloadManager 发，
        // 不给别的 App 伪造一次"下载完成"来戳我们那个按 id 查表的接收器。
        IntentFilter downloadFilter = new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(downloadReceiver, downloadFilter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(downloadReceiver, downloadFilter);
        }

        // 静态快捷方式那条 XML 链路（android:data 能不能被系统解析器读出来）本机没实机可证，
        // 所以冷启动自检一次：读不出参数就用 Java 建的动态快捷方式补上。那是几次 binder 调用，
        // 扔后台线程——首屏时间不该由长按菜单里那两个入口买单。
        final Context appContext = getApplicationContext();
        new Thread(new Runnable() {
            @Override public void run() {
                ShortcutFallback.verifyAndRepair(appContext);
            }
        }, "shortcut-check").start();

        webview.loadUrl(BuildConfig.APP_URL);
    }

    /**
     * 助手【已经开着】时再从分享面板 / 桌面组件 / 长按图标快捷方式进来一次：
     * ShareActivity 带的是 CLEAR_TOP + SINGLE_TOP，系统复用这个 WebView 实例
     * （重建它等于把正聊到一半的对话、正在流式输出的回答整个丢掉），extras 就只走这里。
     *
     * <p>必须 setIntent：不然 getIntent() 永远停在冷启动那一条上，之后任何读它的代码
     * 都会把上一条分享再发一遍。转发用的还是桥那个 queueStartupEvent，所以非法 id
     * 与不认识的 open_from 一样在这里被静默丢掉。
     */
    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (bridge != null) bridge.queueStartupEvent(intent);
        maybeCheckUpdate(intent);
    }

    /**
     * 只有【用户自己点过】那颗按钮才去问一次发布服务器。
     *
     * <p>这条判据是整个功能的边界：不点就没有新包，所以 onCreate / onResume / BootReceiver /
     * 组件刷新里都不许出现 {@link #startUpdateCheck()}。代价也如实写着——它拿的是
     * GitHub 未鉴权接口（按来源 IP 60 次/小时），自动化的话每天烧掉一格，
     * 而"重启电脑＝给所有手机热修复"这条性质本来就不归它管（界面永远是从服务器现加载的）。
     */
    private void maybeCheckUpdate(Intent intent) {
        if (intent == null) return;
        if (ShellEvents.isCheckUpdateLaunch(intent.getStringExtra("open_from"),
                intent.getDataString())) {
            requestUpdateCheck();
        }
    }

    /**
     * 三条入口共用的那一个门：长按图标的快捷方式、桌面组件那颗按钮、设置里那一行
     * （{@code ShellBridge.checkUpdate}）。
     *
     * <p>{@code startUpdateCheck()} 全仓只有这里调一次，所以"有没有人绕过用户动作自己查"
     * 这件事是可数的——见 backend/tests/test_android_shell.py 那条锁。
     */
    void requestUpdateCheck() {
        if (isFinishing() || isDestroyed()) return;
        startUpdateCheck();
    }

    // ---------- 检查更新：拉发布页 → 确认 → 下载 → 交给系统安装 ----------

    private static final int UPDATE_CONNECT_MS = 8000;
    private static final int UPDATE_READ_MS = 12000;
    /** 一条 release JSON 十几 KB 封顶；读满这个数还不停手就说明回来的不是它。 */
    private static final int UPDATE_MAX_BYTES = 256 * 1024;

    /** 后台线程里跑，回主线程弹框——网络绝不能在 UI 线程上碰（首屏时间与 524 那次的同一类错）。 */
    private void startUpdateCheck() {
        if (updateCheckRunning) {
            toast("已经在检查了，稍等一下");
            return;
        }
        updateCheckRunning = true;
        toast("正在检查更新…");
        final Context app = getApplicationContext();
        new Thread(new Runnable() {
            @Override public void run() {
                ReleasePlan.Decision decision;
                try {
                    decision = ReleasePlan.decide(BuildConfig.VERSION_NAME, fetchLatestRelease(app));
                } catch (Exception e) {
                    // 连不上/超时/被网关改了：说清是哪一种，绝不当成"已经是最新版"
                    decision = ReleasePlan.unusable("连不上发布服务器（"
                            + e.getClass().getSimpleName() + "）");
                }
                updateCheckRunning = false;
                final ReleasePlan.Decision shown = decision;
                runOnUiThread(new Runnable() {
                    @Override public void run() { showUpdateResult(shown); }
                });
            }
        }, "update-check").start();
    }

    private String fetchLatestRelease(Context app) throws Exception {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(ReleasePlan.LATEST_URL).openConnection();
            conn.setConnectTimeout(UPDATE_CONNECT_MS);
            conn.setReadTimeout(UPDATE_READ_MS);
            conn.setInstanceFollowRedirects(true);
            conn.setRequestMethod("GET");
            // GitHub 的 API 对没有 User-Agent 的请求直接回 403，这不是可选装饰
            conn.setRequestProperty("User-Agent", "ai-assistant-shell/" + BuildConfig.VERSION_NAME);
            conn.setRequestProperty("Accept", "application/vnd.github+json");
            int status = conn.getResponseCode();
            if (status != HttpURLConnection.HTTP_OK) {
                throw new java.io.IOException("HTTP " + status);
            }
            InputStream in = conn.getInputStream();
            try {
                ByteArrayOutputStream buf = new ByteArrayOutputStream(8 * 1024);
                byte[] chunk = new byte[8 * 1024];
                int read;
                int total = 0;
                while ((read = in.read(chunk)) > 0) {
                    total += read;
                    if (total > UPDATE_MAX_BYTES) {
                        throw new java.io.IOException("返回体积异常");
                    }
                    buf.write(chunk, 0, read);
                }
                return buf.toString("UTF-8");
            } finally {
                in.close();
            }
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private void showUpdateResult(ReleasePlan.Decision decision) {
        if (isFinishing() || isDestroyed()) return;      // 对话框挂在已经没了的窗口上是 BadTokenException
        if (decision.kind == ReleasePlan.Kind.AVAILABLE) {
            confirmDownload(decision);
            return;
        }
        String message = decision.kind == ReleasePlan.Kind.UP_TO_DATE
                ? "已经是最新版 v" + decision.version
                : "检查更新失败：" + decision.reason;
        new AlertDialog.Builder(this)
                .setTitle("检查更新")
                .setMessage(message)
                .setPositiveButton("好", null)
                .show();
    }

    /** 先问一句再动流量：查出新版 ≠ 立刻装，这是这个功能对"不点就不给包"那条承诺的下半段。 */
    private void confirmDownload(final ReleasePlan.Decision decision) {
        StringBuilder text = new StringBuilder();
        text.append("当前 v").append(BuildConfig.VERSION_NAME)
                .append(" → 最新 v").append(decision.version);
        if (decision.sizeBytes > 0) {
            text.append("，约 ").append(Math.max(1, decision.sizeBytes / 1024)).append(" KB");
        }
        if (decision.notes != null && !decision.notes.isEmpty()) {
            text.append("\n\n").append(decision.notes);
        }
        new AlertDialog.Builder(this)
                .setTitle("发现新版本 v" + decision.version)
                .setMessage(text)
                .setPositiveButton("下载", (dialog, which) -> startApkDownload(decision))
                .setNegativeButton("以后再说", null)
                .show();
    }

    private void startApkDownload(ReleasePlan.Decision decision) {
        String fileName = ReleasePlan.assetName(decision.version);
        try {
            DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            if (manager == null) {
                toast("这台设备不支持下载");
                return;
            }
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(decision.url));
            request.setMimeType(APK_MIME);
            // 落在本应用自己的外部目录，不落公共「下载」：
            // ① DownloadManager.Request.allowOverwrite 是 @hide 的，公开 API 里没有"覆盖"这个开关，
            //    而同一版重下（上一次没装完）撞已存在文件就会失败；
            // ② Android 10+ 的分区存储下，公共目录里的同名文件我们未必删得动，自己目录里的删得动；
            // ③ 安装包不是给用户留着看的资料，卸掉应用就该跟着走。
            File dir = getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
            if (dir != null) {
                File stale = new File(dir, fileName);
                if (stale.isFile()) {
                    stale.delete();          // 删不掉也继续：真撞上了下面那句会给出失败提示
                }
            }
            request.setDestinationInExternalFilesDir(this, Environment.DIRECTORY_DOWNLOADS, fileName);
            request.setNotificationVisibility(
                    DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            pendingApkId = manager.enqueue(request);
            pendingApkVersion = decision.version;
            toast("开始下载 v" + decision.version);
        } catch (Exception e) {
            toast("下载失败：" + e.getMessage());
        }
    }

    /**
     * 下载完成后把安装包交给系统安装页。
     *
     * <p>URI 用 {@code getUriForDownloadedFile} 而不是自己拼 file://：Android 7 起
     * 跨进程给 file:// 会抛 FileUriExposedException，而 content:// 由 DownloadManager 自己
     * 授权，省掉一个 FileProvider（它在 androidx 里，会破零依赖）。
     */
    private void launchInstaller(DownloadManager manager, long id) {
        String label = pendingApkVersion != null ? "v" + pendingApkVersion : "新版本";
        Uri apkUri = null;
        try {
            apkUri = manager.getUriForDownloadedFile(id);
        } catch (Exception ignored) {
            // 拿不到就往下走那句"找不到文件"，别在这里抛出去把接收器带崩
        }
        if (apkUri == null) {
            toast("下载完成了，但找不到那个文件");
            return;
        }
        Intent install = new Intent(Intent.ACTION_VIEW);
        install.setDataAndType(apkUri, APK_MIME);
        install.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            startActivity(install);
        } catch (Exception e) {
            // 这台手机还没允许本应用"安装未知应用"，系统不会替你打开那个开关
            offerUnknownSourcesSettings(label);
        }
    }

    private void offerUnknownSourcesSettings(final String label) {
        if (isFinishing() || isDestroyed()) return;
        new AlertDialog.Builder(this)
                .setTitle("装不了：" + label)
                .setMessage("这台手机还没允许「AI 助手」安装应用。打开那个开关后再回来点一次下载就行。")
                .setPositiveButton("去设置", (dialog, which) -> openInstallPermissionSettings())
                .setNegativeButton("取消", null)
                .show();
    }

    private void openInstallPermissionSettings() {
        try {
            Intent page = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + getPackageName()));
            page.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(page);
        } catch (Exception e) {
            // 个别 ROM 没实现那一页；退到应用详情页，至少人能找到开关
            try {
                Intent fallback = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.parse("package:" + getPackageName()));
                fallback.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(fallback);
            } catch (Exception stillNothing) {
                toast("系统没有给出可以改这个开关的页面");
            }
        }
    }

    /**
     * 把网页给出的下载请求落到公共「下载」目录。
     *
     * WebView 对 attachment 响应默认不做任何处理，所以这里显式交给系统 DownloadManager；
     * 走公共目录由系统负责写入，无需申请任何运行时权限，也不要往清单里加 WRITE_EXTERNAL_STORAGE。
     */
    private void startDownload(String url, String userAgent, String contentDisposition) {
        Uri uri = Uri.parse(url);
        String scheme = uri.getScheme();
        // 只放行 http/https；file、content、javascript 等一律忽略，绝不把它丢给任意 Intent
        if (scheme == null || !(scheme.equalsIgnoreCase("http") || scheme.equalsIgnoreCase("https"))) {
            return;
        }

        String fileName = fileNameFromDisposition(contentDisposition);
        if (fileName == null) {
            fileName = sanitizeFileName(uri.getLastPathSegment());
        }
        if (fileName == null) {
            fileName = "ai-assistant-" + System.currentTimeMillis();
        }

        try {
            DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            if (manager == null) {
                toast("这台设备不支持下载");
                return;
            }
            DownloadManager.Request request = new DownloadManager.Request(uri);
            if (userAgent != null && !userAgent.isEmpty()) {
                request.addRequestHeader("User-Agent", userAgent);
            }
            // API 9 起就有，minSdk 23 直接可用；比 API 33 的 setDestinationDirectoryPath 更稳
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, fileName);
            request.setNotificationVisibility(
                    DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            long id = manager.enqueue(request);
            pendingDownloads.put(id, fileName);
        } catch (Exception e) {
            // 存储不可用、目录被拒等：不要静默失败
            toast("下载失败：" + e.getMessage());
        }
    }

    /** 完成/失败都要有反馈：Toast 提示，别让用户对着没反应的界面猜。 */
    private final BroadcastReceiver downloadReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            long id = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1L);
            String name = pendingDownloads.remove(id);
            String label = name != null ? name : "文件";
            int status = -1;
            try {
                DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
                if (manager != null) {
                    Cursor cursor = manager.query(new DownloadManager.Query().setFilterById(id));
                    if (cursor != null) {
                        try {
                            if (cursor.moveToFirst()) {
                                status = cursor.getInt(
                                        cursor.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS));
                            }
                        } finally {
                            cursor.close();
                        }
                    }
                }
            } catch (Exception ignored) {
                // 查不到状态时按失败处理，宁可多提醒也别静默
            }
            if (id == pendingApkId) {
                // 安装包这一条不能落到下面那句"已保存到「下载」"里：那样等于下载完了却没人去起安装页
                pendingApkId = -1L;
                String version = pendingApkVersion;
                pendingApkVersion = null;
                if (status != DownloadManager.STATUS_SUCCESSFUL) {
                    toast("下载失败：v" + (version != null ? version : "新版本"));
                    return;
                }
                DownloadManager manager = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
                if (manager != null) {
                    launchInstaller(manager, id);
                } else {
                    toast("下载完成了，但这台设备取不到那个文件");
                }
                return;
            }
            if (status == DownloadManager.STATUS_SUCCESSFUL) {
                toast("已保存到「下载」：" + label);
            } else if (status == DownloadManager.STATUS_FAILED) {
                toast("下载失败：" + label);
            }
        }
    };

    /** 从响应头取文件名，取不到返回 null 交给调用方回退。 */
    private static String fileNameFromDisposition(String contentDisposition) {
        if (contentDisposition == null || contentDisposition.isEmpty()) {
            return null;
        }
        Matcher matcher = DISPOSITION_FILENAME.matcher(contentDisposition);
        while (matcher.find()) {
            String value = matcher.group(1) != null ? matcher.group(1) : matcher.group(2);
            if (value == null) {
                continue;
            }
            value = value.trim();
            int sep = value.indexOf("''"); // RFC 5987：UTF-8''%E4%BD%A0...
            if (sep >= 0) {
                try {
                    value = URLDecoder.decode(value.substring(sep + 2), "UTF-8");
                } catch (Exception ignored) {
                    value = value.substring(sep + 2);
                }
            }
            String cleaned = sanitizeFileName(value);
            if (cleaned != null) {
                return cleaned;
            }
        }
        return null;
    }

    /**
     * 清洗服务器给的文件名：不把原样字符串拼进路径。
     * 去掉路径分隔符（只保留最后一段）、控制字符与文件系统非法字符，并限制长度。
     */
    private static String sanitizeFileName(String raw) {
        if (raw == null) {
            return null;
        }
        String name = raw;
        int slash = Math.max(name.lastIndexOf('/'), name.lastIndexOf('\\'));
        if (slash >= 0) {
            name = name.substring(slash + 1);
        }
        StringBuilder sb = new StringBuilder(name.length());
        for (int i = 0; i < name.length(); i++) {
            char c = name.charAt(i);
            if (c < 0x20 || c == 0x7F) {
                continue; // 控制字符直接丢弃
            }
            if (c == '/' || c == '\\' || c == ':' || c == '*' || c == '?'
                    || c == '"' || c == '<' || c == '>' || c == '|') {
                sb.append('_'); // 非法字符替换，避免逃出目录或报错
            } else {
                sb.append(c);
            }
        }
        name = sb.toString().trim();
        if (name.isEmpty() || name.equals(".") || name.equals("..")) {
            return null;
        }
        if (name.length() > 128) {
            name = name.substring(0, 128);
        }
        return name;
    }

    private void toast(final String message) {
        runOnUiThread(() -> Toast.makeText(MainActivity.this, message, Toast.LENGTH_SHORT).show());
    }

    /** 网页标了只要图片时就不要给出一堆无关类型。 */
    private static boolean acceptIsImageOnly(String[] acceptTypes) {
        if (acceptTypes == null || acceptTypes.length == 0) {
            return false;
        }
        for (String type : acceptTypes) {
            if (type == null || type.isEmpty()) {
                continue;
            }
            if (!type.startsWith("image/")) {
                return false;
            }
        }
        return true;
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode != CAMERA_PERMISSION_CODE || pendingCameraRequest == null) {
            // 通知那颗（NOTIFICATION_PERMISSION_CODE）故意落在这里什么都不做：它没有
            // WebView 的 PermissionRequest 要批准或拒绝，结果只影响"到点发不发得出去"，
            // 而那一行每次显示时都现问 PermissionStatus——在这里再推一份状态就是第二份真相。
            return;
        }
        PermissionRequest request = pendingCameraRequest;
        pendingCameraRequest = null;
        boolean granted = results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED;
        if (granted) {
            // 同样只回白名单内的资源：网页在这条请求里夹带的其它类型不因"用户点了
            // 允许摄像头"而被顺带批准。
            String[] allowed = allowedResources(request);
            if (allowed.length > 0) {
                request.grant(allowed);
            } else {
                request.deny();
            }
        } else {
            request.deny();
        }
    }

    /** 网页请求的资源里，壳只认摄像头这一种（运行时权限也只为它弹过系统框）。 */
    private static String[] allowedResources(PermissionRequest request) {
        List<String> allowed = new ArrayList<>();
        for (String res : request.getResources()) {
            if (PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(res)) {
                allowed.add(res);
            }
        }
        return allowed.toArray(new String[0]);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != FILE_CHOOSER_CODE) {
            return;
        }
        ValueCallback<Uri[]> callback = filePathCallback;
        filePathCallback = null;
        if (callback == null) {
            return;
        }
        if (resultCode != RESULT_OK || data == null) {
            callback.onReceiveValue(null);   // 必须回一次，否则选择器再也打不开
            return;
        }

        Uri single = data.getData();
        if (single != null) {
            persist(single);
            callback.onReceiveValue(new Uri[]{single});
            return;
        }
        if (data.getClipData() != null) {
            int count = data.getClipData().getItemCount();
            Uri[] uris = new Uri[count];
            for (int i = 0; i < count; i++) {
                uris[i] = data.getClipData().getItemAt(i).getUri();
                persist(uris[i]);
            }
            callback.onReceiveValue(uris);
            return;
        }
        callback.onReceiveValue(null);
    }

    private void persist(Uri uri) {
        try {
            getContentResolver().takePersistableUriPermission(uri,
                    Intent.FLAG_GRANT_READ_URI_PERMISSION);
        } catch (Exception ignored) {
            // 个别 Provider 不支持持久授权，本次会话内仍可读
        }
    }

    private String offlineHtml() {
        return "<!doctype html><meta charset=utf-8>"
                + "<meta name=viewport content='width=device-width,initial-scale=1'>"
                + "<body style='background:#0e1013;color:#e6e8ee;font-family:sans-serif;"
                + "display:flex;min-height:92vh;align-items:center;justify-content:center'>"
                + "<div style='text-align:center;padding:24px;max-width:30em'>"
                + "<div style='font-size:34px'>🔌</div>"
                + "<h2 style='font-size:17px;margin:10px 0'>连不上助手服务</h2>"
                + "<p style='color:#9aa2b1;font-size:14px;line-height:1.7'>"
                + "助手跑在电脑的本地服务上，需要先开机并启动后端与隧道。</p>"
                + "<a href='" + BuildConfig.APP_URL + "' style='display:inline-block;"
                + "margin-top:18px;padding:10px 22px;border-radius:999px;background:#4f46e5;"
                + "color:#fff;text-decoration:none;font-size:14px'>重试</a>"
                + "</div></body>";
    }

    @Override
    public void onBackPressed() {
        if (webview.canGoBack()) {
            webview.goBack();
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onPause() {
        super.onPause();
        webview.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        // 提醒表的「重读」不在这里做，而在 ShellBridge 每次碰表之前做一次：
        // 到点通知是 ReminderReceiver 另起一个 store 实例写的（整块覆盖），而网页随时可能在
        // 前台问一句 listReminders()，那时还没走 onResume。放在桥里才真的堵住这个窗口。
        webview.onResume();
    }

    @Override
    protected void onDestroy() {
        if (bridge != null) {
            bridge.release();        // 静态引用要放开，否则整棵 WebView 树跟着 Activity 一起漏
        }
        if (filePathCallback != null) {
            filePathCallback.onReceiveValue(null);
            filePathCallback = null;
        }
        if (pendingCameraRequest != null) {
            pendingCameraRequest.deny();
            pendingCameraRequest = null;
        }
        try {
            unregisterReceiver(downloadReceiver);
        } catch (Exception ignored) {
            // 没注册成功过（理论上不会）就别因收尾再抛
        }
        pendingDownloads.clear();
        webview.destroy();
        super.onDestroy();
    }
}
