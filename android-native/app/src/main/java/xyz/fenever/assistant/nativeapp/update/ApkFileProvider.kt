package xyz.fenever.assistant.nativeapp.update

import android.content.ContentProvider
import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.MatrixCursor
import android.net.Uri
import android.os.Environment
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import java.io.File
import java.io.FileNotFoundException

/**
 * 把【本应用自己下载的更新包】以 content:// 交给系统安装页（v0.23 T1.7）。
 *
 * 与旧壳 android/.../ApkFileProvider.java 同一套边界，三条少一处都是一个可读任意路径的口子：
 * ① manifest 里 exported=false——除了我们主动 grant 的接收方，谁也解析不到这个 authority；
 * ② 只接受单段路径，canonical 后必须仍落在外部私有下载目录内（挡 ../ 与软链接逃逸），
 *    且文件名必须是 ai-assistant-native-*.apk——半截 .part 与顺手别的文件都递不出去；
 * ③ 只给读，openFile 收非 "r" 模式直接抛。
 *
 * 不引 androidx.core 的 FileProvider：一个目录一个形状的读取，手写的表面比配置
 * file_paths.xml 再包一层更小；旧壳同一取舍。
 */
class ApkFileProvider : ContentProvider() {

    override fun onCreate() = true

    /** 安装包落的那个目录——与 Updater 下载写文件的是同一个表达式，不另起第二处真相。 */
    private fun rootDir(): File? =
        context?.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)

    override fun query(uri: Uri, projection: Array<String>?, selection: String?,
                       selectionArgs: Array<String>?, sortOrder: String?): Cursor? {
        val file = try {
            resolve(uri)
        } catch (e: Exception) {
            null
        } ?: return null
        // 安装页只问得动这两列（显示名与体积）；其它列一律不奉陪，最小表面。
        return MatrixCursor(arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE)).apply {
            addRow(arrayOf<Any>(file.name, file.length()))
        }
    }

    override fun getType(uri: Uri): String = APK_MIME

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor {
        if (mode != "r") throw FileNotFoundException("这个文件只读：$mode")
        val file = try {
            resolve(uri)
        } catch (e: Exception) {
            null
        } ?: throw FileNotFoundException("没有这个文件")
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
    }

    /** 解析并证明它没逃出根目录、且确实是那一个更新包。返回 null = 形状不对或文件不在。 */
    private fun resolve(uri: Uri): File? {
        if (uri.pathSegments.size != 1) return null
        val name = uri.lastPathSegment
        if (name.isNullOrEmpty() || name == "." || name == "..") return null
        if (!name.startsWith(APK_NAME_PREFIX) || !name.endsWith(".apk")) return null
        val root = rootDir() ?: return null
        val candidate = File(root, name)
        val rootPath = root.canonicalPath
        // canonical 之后再验一次：name 里塞不进 '/'，但软链接与大小写花活都在这一步现形
        if (!candidate.canonicalPath.startsWith(rootPath + File.separator)) return null
        if (!candidate.isFile) return null
        return candidate
    }

    // ---------- 下面三个不在这个功能的存在理由里，全部拒掉而不是装忙 ----------

    override fun insert(uri: Uri, values: ContentValues?): Uri? =
        throw UnsupportedOperationException()

    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<String>?): Int =
        throw UnsupportedOperationException()

    override fun update(uri: Uri, values: ContentValues?, selection: String?,
                        selectionArgs: Array<String>?): Int =
        throw UnsupportedOperationException()

    companion object {
        /** manifest 里注册的是 `${applicationId}.apkprovider`；拼 URI 用同一个后缀常量。 */
        const val AUTHORITY_SUFFIX = ".apkprovider"
        const val APK_NAME_PREFIX = "ai-assistant-native-"
        const val APK_MIME = "application/vnd.android.package-archive"

        fun uriForFile(context: Context, fileName: String): Uri =
            Uri.Builder().scheme("content")
                .authority(context.packageName + AUTHORITY_SUFFIX)
                .path(fileName)
                .build()
    }
}
