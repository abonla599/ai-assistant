package xyz.fenever.assistant;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.ValueCallback;

/**
 * AI 助手的 WebView 外壳。
 *
 * 界面与后端全部在线上（https://ai.fenever.xyz/app/），壳只负责三件网页自己做不到的事：
 * 给 WebView 提供原生文件选择、把网页的摄像头请求转成运行时权限、以及断网时给出可读提示。
 * 因此改界面不需要重新打包 APK。
 */
public class MainActivity extends Activity {

    private static final int FILE_CHOOSER_CODE = 1001;
    private static final int CAMERA_PERMISSION_CODE = 1002;

    private WebView webview;
    private ValueCallback<Uri[]> filePathCallback;
    private PermissionRequest pendingCameraRequest;

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

        webview.setWebViewClient(new WebViewClient() {
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

        webview.loadUrl(BuildConfig.APP_URL);
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
        webview.onResume();
    }

    @Override
    protected void onDestroy() {
        if (filePathCallback != null) {
            filePathCallback.onReceiveValue(null);
            filePathCallback = null;
        }
        if (pendingCameraRequest != null) {
            pendingCameraRequest.deny();
            pendingCameraRequest = null;
        }
        webview.destroy();
        super.onDestroy();
    }
}
