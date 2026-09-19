package xyz.fenever.assistant;

import android.app.Activity;
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
import android.os.Bundle;
import android.os.Environment;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.ValueCallback;
import android.widget.Toast;

import java.io.File;
import java.net.URLDecoder;
import java.util.HashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import xyz.fenever.assistant.core.ReminderStore;
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

    private WebView webview;
    private ValueCallback<Uri[]> filePathCallback;
    private PermissionRequest pendingCameraRequest;

    // 网页与原生之间唯一的口子，以及它背后的两张表（都从 SharedPreferences 恢复）
    private ShellBridge bridge;
    private ReminderStore reminderStore;
    private ShareInbox shareInbox;

    // DownloadManager 的 enqueue id -> 展示用的文件名；回调线程与接收器都在主线程，普通 HashMap 够用
    private final Map<Long, String> pendingDownloads = new HashMap<>();

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

        // 原生能力（提醒/分享入口）唯一的入口：一个对象、八个方法，见 ShellBridge 的类注释。
        // 通知渠道要先建好——第一次设提醒时发的那条通知没有渠道会在 Android 8+ 上直接发不出去。
        NotificationChannels.ensure(this);
        reminderStore = new ReminderStore(PrefsIo.reminders(this));
        // 分享件走 cacheDir：系统随时可能清掉它，所以 ShareInbox 每次读都先看文件还在不在
        shareInbox = new ShareInbox(new File(getCacheDir(), "shares"), PrefsIo.shares(this));
        bridge = new ShellBridge(this, webview, reminderStore, shareInbox);
        webview.addJavascriptInterface(bridge, "AssistantShell");
        // 冷启动带进来的 extra（点通知、从分享面板进来）先攒着，等页面加载完再发给网页
        bridge.queueStartupEvent(getIntent());

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
                runOnUiThread(() -> request.grant(request.getResources()));
            }
        });

        webview.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) ->
                startDownload(url, userAgent, contentDisposition));

        // ACTION_DOWNLOAD_COMPLETE 只由系统 DownloadManager 发出，属系统广播，
        // 因此即便 targetSdk 34 也不必给 registerReceiver 传 RECEIVER_EXPORTED 标志。
        registerReceiver(downloadReceiver, new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE));

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
            return;
        }
        PermissionRequest request = pendingCameraRequest;
        pendingCameraRequest = null;
        boolean granted = results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED;
        if (granted) {
            request.grant(request.getResources());
        } else {
            request.deny();
        }
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
