package xyz.fenever.assistant;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.ParcelFileDescriptor;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.Environment;
import android.provider.OpenableColumns;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;

/**
 * 把【壳自己下载的更新包】以 content:// 交给系统安装页。
 *
 * <p>为什么不引 androidx.core 的 FileProvider：壳的零第三方依赖纪律（见 build.gradle）。
 * 因此这一层是手写的，写小：只读、只开一个目录、URI 形状只有一个段。
 *
 * <p>安全边界写死在三处，少一处都是一个可读任意路径的口子：
 * ① exported=false——除了我们主动 grant 的接收方，谁也解析不到这个 authority；
 * ② 只接受单段路径，取 canonical 后必须仍落在那个外部私有目录内（挡 ../ 与软链接逃逸）；
 * ③ 只给读，openFile 收非 "r" 模式直接抛。
 */
public final class ApkFileProvider extends ContentProvider {

    /** manifest 里注册的是 `${applicationId}.apkprovider`；拼 URI 用同一个后缀常量。 */
    public static final String AUTHORITY_SUFFIX = ".apkprovider";

    static Uri uriForFile(Context context, String fileName) {
        return new Uri.Builder().scheme("content")
                .authority(context.getPackageName() + AUTHORITY_SUFFIX)
                .path(fileName)
                .build();
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    /** 安装包落的那个目录——与 MainActivity 下载写文件的是同一个表达式，不另起第二处真相。 */
    private File rootDir() {
        Context context = getContext();
        return context == null ? null
                : context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS);
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection,
                        String[] selectionArgs, String sortOrder) {
        File file;
        try {
            file = resolve(uri);
        } catch (Exception e) {
            return null;
        }
        if (file == null) {
            return null;
        }
        // 安装页只问得动这两列（显示名与体积）；其它列一律不奉陪，最小表面。
        MatrixCursor cursor = new MatrixCursor(new String[]{
                OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE});
        cursor.addRow(new Object[]{file.getName(), file.length()});
        return cursor;
    }

    @Override
    public String getType(Uri uri) {
        return "application/vnd.android.package-archive";
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) {
            throw new FileNotFoundException("这个文件只读：" + mode);
        }
        File file;
        try {
            file = resolve(uri);
        } catch (IOException e) {
            throw new FileNotFoundException("路径解析失败");
        }
        if (file == null) {
            throw new FileNotFoundException("没有这个文件");
        }
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    /** 解析并证明它没逃出根目录。返回 null = 形状不对或文件不在。 */
    private File resolve(Uri uri) throws IOException {
        if (uri.getPathSegments().size() != 1) {
            return null;
        }
        String name = uri.getLastPathSegment();
        if (name == null || name.isEmpty() || name.equals(".") || name.equals("..")) {
            return null;
        }
        File root = rootDir();
        if (root == null) {
            return null;
        }
        File candidate = new File(root, name);
        String rootPath = root.getCanonicalPath();
        String filePath = candidate.getCanonicalPath();
        // canonical 之后再验一次：name 里塞不进 '/'，但软链接与大小写花活都在这一步现形
        if (!filePath.startsWith(rootPath + File.separator) || !candidate.isFile()) {
            return null;
        }
        return candidate;
    }

    // ---------- 下面三个不在这个功能的存在理由里，全部拒掉而不是装忙 ----------

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException();
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException();
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException();
    }
}
