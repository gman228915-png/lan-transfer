package com.lantransfer

import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.view.View
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.zxing.integration.android.IntentIntegrator
import com.lantransfer.databinding.ActivityMainBinding
import kotlinx.coroutines.*
import java.io.File
import java.io.FileOutputStream
import kotlin.math.max

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private var api: ApiClient? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private lateinit var adapter: FileAdapter
    private val mainHandler = Handler(Looper.getMainLooper())

    /** 正在连接 / 上传 / 下载，防止重复操作 */
    private var opRunning = false
    private var lastProgressAt = 0L

    /** 下载到应用专属外部存储 —— 所有 Android 版本都无需任何权限就能读写 */
    private val downloadDir: File by lazy {
        // getExternalFilesDir 返回的路径形如 /storage/emulated/0/Android/data/com.lantransfer/files/Download/
        getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS)
            ?: File(filesDir, "Download")
    }

    private val pickLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            val uris = mutableListOf<Uri>()
            result.data?.clipData?.let { clip ->
                for (i in 0 until clip.itemCount) {
                    clip.getItemAt(i)?.uri?.let { uris.add(it) }
                }
            }
            if (uris.isEmpty()) result.data?.data?.let { uris.add(it) }
            if (uris.isNotEmpty()) uploadUris(uris)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        adapter = FileAdapter(emptyList(), { f -> downloadFile(f) }, { f -> confirmDelete(f) })
        binding.rvFiles.layoutManager = LinearLayoutManager(this)
        binding.rvFiles.adapter = adapter

        binding.btnConnect.setOnClickListener { connect() }
        binding.btnUpload.setOnClickListener { pickFile() }
        binding.btnScan.setOnClickListener { scan() }
        binding.btnOpenDownloads.setOnClickListener { showDownloadOptions() }

        // 记住上次地址与口令；有记录时自动重连
        val sp = getSharedPreferences("lantransfer", MODE_PRIVATE)
        val savedUrl = sp.getString("url", "") ?: ""
        val savedToken = sp.getString("token", "") ?: ""
        if (savedUrl.isNotEmpty()) binding.etUrl.setText(savedUrl)
        if (savedToken.isNotEmpty()) binding.etToken.setText(savedToken)
        if (savedUrl.isNotEmpty() && savedToken.isNotEmpty()) {
            binding.tvStatus.text = "正在连接上次的电脑…"
            connect()
        } else {
            binding.tvStatus.text = "未连接 · 扫码或输入地址后点连接"
        }
    }

    // --------------------------------------------------------------------------- #
    // 扫码
    // --------------------------------------------------------------------------- #
    private fun scan() {
        IntentIntegrator(this)
            .setPrompt("扫描电脑端二维码")
            .setDesiredBarcodeFormats(IntentIntegrator.QR_CODE)
            .setOrientationLocked(false)
            .initiateScan()
    }

    @Deprecated("Deprecated in Java")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == IntentIntegrator.REQUEST_CODE) {
            val result = IntentIntegrator.parseActivityResult(requestCode, resultCode, data)
            if (result != null && result.contents != null) {
                handleScanResult(result.contents)
            }
        }
    }

    private fun handleScanResult(text: String) {
        val uri = Uri.parse(text)
        val host = uri.host
        if (host.isNullOrBlank()) {
            Toast.makeText(this, "无法识别二维码内容", Toast.LENGTH_SHORT).show()
            return
        }
        val scheme = if (uri.scheme?.lowercase() == "http") "http" else "https"
        val port = if (uri.port > 0) uri.port else 8765
        val token = uri.getQueryParameter("k") ?: ""
        val base = "$scheme://$host:$port"
        binding.etUrl.setText(base)
        binding.etToken.setText(token)
        if (token.isEmpty()) {
            Toast.makeText(this, "二维码里没有口令，请输入电脑端显示的口令", Toast.LENGTH_LONG).show()
            return
        }
        Toast.makeText(this, "已识别：$base", Toast.LENGTH_SHORT).show()
        connect()
    }

    // --------------------------------------------------------------------------- #
    // 连接
    // --------------------------------------------------------------------------- #
    private fun connect() {
        if (opRunning) {
            Toast.makeText(this, "正在传输，请稍候", Toast.LENGTH_SHORT).show()
            return
        }
        val url = binding.etUrl.text.toString().trim().trimEnd('/')
        var token = binding.etToken.text.toString().trim()

        if (url.isEmpty()) {
            Toast.makeText(this, "请输入电脑端地址，如 https://192.168.1.10:8765",
                Toast.LENGTH_SHORT).show()
            return
        }

        // 如果用户贴了完整 URL，提取 token
        if (url.contains("?k=") && token.isEmpty()) {
            val parts = url.split("?k=")
            binding.etUrl.setText(parts[0].trimEnd('/'))
            token = parts.getOrNull(1) ?: ""
            binding.etToken.setText(token)
        }
        if (token.isEmpty()) {
            Toast.makeText(this, "请输入口令（电脑端窗口里能看到）", Toast.LENGTH_SHORT).show()
            return
        }

        val api = ApiClient(url, token, this)
        binding.tvStatus.text = "连接中…"

        scope.launch {
            val ok = api.testConnection()
            withContext(Dispatchers.Main) {
                if (ok) {
                    this@MainActivity.api = api
                    getSharedPreferences("lantransfer", MODE_PRIVATE)
                        .edit().putString("url", url).putString("token", token).apply()
                    binding.tvStatus.text = "已连接 ✓"
                    refreshFiles()
                } else {
                    val msg = api.lastError ?: "无法连接到服务器"
                    binding.tvStatus.text = "连接失败"
                    if (api.pinMismatch) {
                        AlertDialog.Builder(this@MainActivity)
                            .setTitle("证书指纹变化")
                            .setMessage(msg)
                            .setPositiveButton("清除并重连") { _, _ ->
                                api.resetPin()
                                connect()
                            }
                            .setNegativeButton("取消", null)
                            .show()
                    } else {
                        Toast.makeText(this@MainActivity, msg, Toast.LENGTH_LONG).show()
                    }
                }
            }
        }
    }

    private fun refreshFiles() {
        val api = api ?: return
        scope.launch {
            val list = api.listFiles()
            withContext(Dispatchers.Main) {
                adapter.update(list)
                binding.tvInfo.text = "共 ${list.size} 个文件"
                binding.tvEmpty.visibility = if (list.isEmpty()) View.VISIBLE else View.GONE
                if (list.isEmpty() && api.lastError != null) {
                    binding.tvStatus.text = api.lastError
                }
            }
        }
    }

    // --------------------------------------------------------------------------- #
    // 上传（SAF，不需要任何存储权限；支持多选批量）
    // --------------------------------------------------------------------------- #
    private fun pickFile() {
        api ?: run {
            Toast.makeText(this, "请先连接服务器", Toast.LENGTH_SHORT).show()
            return
        }
        if (opRunning) {
            Toast.makeText(this, "正在传输，请稍候", Toast.LENGTH_SHORT).show()
            return
        }
        val intent = Intent(Intent.ACTION_GET_CONTENT).apply {
            type = "*/*"
            addCategory(Intent.CATEGORY_OPENABLE)
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
        }
        pickLauncher.launch(Intent.createChooser(intent, "选择文件（可多选）"))
    }

    private data class UploadItem(val uri: Uri, val name: String, val size: Long)

    private fun uploadUris(uris: List<Uri>) {
        val api = api ?: run {
            Toast.makeText(this, "请先连接服务器", Toast.LENGTH_SHORT).show()
            return
        }
        if (opRunning) {
            Toast.makeText(this, "正在传输，请稍候", Toast.LENGTH_SHORT).show()
            return
        }

        val items = uris.map {
            UploadItem(it, getFileName(it) ?: "unknown", getFileSize(it))
        }
        val totalBytes = items.sumOf { max(it.size, 0L) }

        scope.launch {
            withContext(Dispatchers.Main) {
                opRunning = true
                showProgress(true)
                binding.btnUpload.isEnabled = false
            }

            val failed = mutableListOf<Uri>()
            var sentBefore = 0L
            var index = 0

            for (item in items) {
                index++
                if (item.size <= 0) {
                    failed.add(item.uri)
                    continue
                }
                withContext(Dispatchers.Main) {
                    binding.tvStatus.text = "上传 $index/${items.size}：${item.name}"
                }

                var ok = false
                try {
                    contentResolver.openInputStream(item.uri)?.use { input ->
                        ok = api.uploadFile(item.name, input, item.size) { done, _ ->
                            updateProgress(sentBefore + done, totalBytes,
                                "上传 $index/${items.size}")
                        }
                    }
                } catch (e: Exception) {
                    ok = false
                }

                if (ok) sentBefore += item.size else failed.add(item.uri)
            }

            withContext(Dispatchers.Main) {
                opRunning = false
                binding.btnUpload.isEnabled = true
                binding.progressBar.progress = if (failed.isEmpty()) 100 else {
                    binding.progressBar.progress
                }
                if (failed.isEmpty()) {
                    binding.tvStatus.text = "上传完成 ✓"
                    Toast.makeText(this@MainActivity,
                        "已上传 ${items.size} 个文件", Toast.LENGTH_SHORT).show()
                    refreshFiles()
                    hideProgressLater()
                } else {
                    val msg = api.lastError ?: "网络异常"
                    binding.tvStatus.text = "失败 ${failed.size}/${items.size} 个"
                    showProgress(false)
                    askRetry("上传失败：$msg\n\n已成功 ${items.size - failed.size} / ${items.size} 个") {
                        uploadUris(failed.toList())
                    }
                }
            }
        }
    }

    // --------------------------------------------------------------------------- #
    // 删除
    // --------------------------------------------------------------------------- #
    private fun confirmDelete(f: FileItem) {
        AlertDialog.Builder(this)
            .setTitle("删除")
            .setMessage("确定删除《${f.name}》吗？")
            .setPositiveButton("删除") { _, _ ->
                val api = api ?: return@setPositiveButton
                scope.launch {
                    val ok = api.deleteFile(f.name)
                    withContext(Dispatchers.Main) {
                        if (ok) {
                            Toast.makeText(this@MainActivity, "已删除", Toast.LENGTH_SHORT).show()
                            refreshFiles()
                        } else {
                            Toast.makeText(this@MainActivity,
                                api.lastError ?: "删除失败", Toast.LENGTH_SHORT).show()
                        }
                    }
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    // --------------------------------------------------------------------------- #
    // 下载（应用专属目录，无需任何存储权限）
    // --------------------------------------------------------------------------- #
    private fun downloadFile(f: FileItem) {
        val api = api ?: return
        if (opRunning) {
            Toast.makeText(this, "正在传输，请稍候", Toast.LENGTH_SHORT).show()
            return
        }
        downloadDir.mkdirs()
        val dest = uniqueLocalFile(sanitizeFileName(f.name))

        scope.launch {
            withContext(Dispatchers.Main) {
                opRunning = true
                showProgress(true)
                binding.tvStatus.text = "下载：${f.name}"
            }

            val ok = api.downloadFile(f.name, dest) { done, total ->
                updateProgress(done, total, "下载 ${f.name}")
            }

            withContext(Dispatchers.Main) {
                opRunning = false
                if (ok) {
                    binding.tvStatus.text = "已下载到：${downloadDir.name}/${dest.name}"
                    binding.progressBar.progress = 100
                    Toast.makeText(this@MainActivity, "下载完成 ✓", Toast.LENGTH_SHORT).show()
                    hideProgressLater()
                } else {
                    binding.tvStatus.text = "下载失败"
                    showProgress(false)
                    askRetry(api.lastError ?: "下载失败，请检查 Wi-Fi") { downloadFile(f) }
                }
            }
        }
    }

    // --------------------------------------------------------------------------- #
    // 进度展示
    // --------------------------------------------------------------------------- #
    private fun updateProgress(done: Long, total: Long, label: String) {
        val now = System.currentTimeMillis()
        if (now - lastProgressAt < 200 && done < total) return
        lastProgressAt = now
        runOnUiThread {
            val pct = if (total > 0) ((done * 100) / total).toInt().coerceIn(0, 100) else 0
            binding.progressBar.progress = pct
            val sizeText = if (total > 0) {
                " · ${FileItem.formatSize(done)} / ${FileItem.formatSize(total)}"
            } else {
                " · ${FileItem.formatSize(done)}"
            }
            binding.tvStatus.text = "$label  $pct%$sizeText"
        }
    }

    private fun showProgress(show: Boolean) {
        binding.progressBar.visibility = if (show) View.VISIBLE else View.GONE
        if (!show) binding.progressBar.progress = 0
        binding.btnConnect.isEnabled = !show
        binding.btnScan.isEnabled = !show
        binding.btnUpload.isEnabled = !show
    }

    private fun hideProgressLater() {
        mainHandler.postDelayed({
            if (!opRunning) showProgress(false)
        }, 1200)
    }

    private fun askRetry(message: String, retry: () -> Unit) {
        AlertDialog.Builder(this)
            .setTitle("操作失败")
            .setMessage(message)
            .setPositiveButton("重试") { _, _ -> retry() }
            .setNegativeButton("取消", null)
            .show()
    }

    private fun uniqueLocalFile(name: String): File {
        var candidate = File(downloadDir, name)
        if (!candidate.exists()) return candidate
        val dot = name.lastIndexOf('.')
        val stem = if (dot > 0) name.substring(0, dot) else name
        val ext = if (dot > 0) name.substring(dot) else ""
        for (i in 1..999) {
            candidate = File(downloadDir, "$stem ($i)$ext")
            if (!candidate.exists()) return candidate
        }
        return File(downloadDir, "$stem-${System.currentTimeMillis()}$ext")
    }

    private fun sanitizeFileName(name: String): String {
        val invalid = Regex("""[\\/:*?"<>|\r\n\t]""")
        val clean = invalid.replace(name, "_").trim().trim('.')
        return clean.ifBlank { "未命名文件" }
    }

    // --------------------------------------------------------------------------- #
    // 已下载文件管理（App 内完成删除/移动/打开/分享，无需任何存储权限）
    // --------------------------------------------------------------------------- #

    /** 待移动到公共目录的文件（SAF 结果回调用） */
    private var pendingMoveFile: File? = null

    /** SAF 另存为：把文件复制到用户自选的公共位置（系统授权，无需存储权限） */
    private val exportLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val file = pendingMoveFile
        pendingMoveFile = null
        val uri = result.data?.data
        if (result.resultCode == Activity.RESULT_OK && file != null && uri != null) {
            scope.launch {
                val ok = runCatching {
                    contentResolver.openOutputStream(uri)?.use { os ->
                        file.inputStream().use { it.copyTo(os) }
                    } != null
                }.getOrDefault(false)
                withContext(Dispatchers.Main) {
                    if (ok) {
                        AlertDialog.Builder(this@MainActivity)
                            .setTitle("已导出")
                            .setMessage("文件已保存到你选择的位置。\n是否删除 App 内的副本？")
                            .setPositiveButton("删除副本") { _, _ ->
                                file.delete()
                                Toast.makeText(this@MainActivity, "已删除", Toast.LENGTH_SHORT).show()
                            }
                            .setNegativeButton("保留", null)
                            .show()
                    } else {
                        Toast.makeText(this@MainActivity, "导出失败", Toast.LENGTH_SHORT).show()
                    }
                }
            }
        }
    }

    private fun showDownloadOptions() {
        downloadDir.mkdirs()
        val files = (downloadDir.listFiles()?.filter { it.isFile } ?: emptyList())
            .sortedByDescending { it.lastModified() }

        if (files.isEmpty()) {
            AlertDialog.Builder(this)
                .setTitle("已下载的文件")
                .setMessage("还没有下载文件。")
                .setPositiveButton("关闭", null)
                .show()
            return
        }

        val names = files.map { "${it.name}  (${FileItem.formatSize(it.length())})" }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle("已下载 ${files.size} 个文件（点击管理）")
            .setItems(names) { _, idx -> showFileActions(files[idx]) }
            .setPositiveButton("全部分享") { _, _ -> shareAllFiles(files) }
            .setNegativeButton("关闭", null)
            .show()
    }

    /** 单个已下载文件的操作菜单 */
    private fun showFileActions(file: File) {
        val actions = arrayOf("打开", "分享", "移动/另存到公共目录", "删除")
        AlertDialog.Builder(this)
            .setTitle(file.name)
            .setItems(actions) { _, idx ->
                when (idx) {
                    0 -> openFile(file)
                    1 -> shareAllFiles(listOf(file))
                    2 -> exportFile(file)
                    3 -> confirmDeleteLocal(file)
                }
            }
            .setNegativeButton("返回", null)
            .show()
    }

    /** 用系统应用打开已下载文件 */
    private fun openFile(file: File) {
        try {
            val uri = FileProvider.getUriForFile(this, "${packageName}.fileprovider", file)
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, guessMime(file))
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            intent.resolveActivity(packageManager)?.let { startActivity(intent) }
                ?: Toast.makeText(this, "没有可打开此文件的应用", Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            Toast.makeText(this, "打开失败: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    /** SAF 导出到公共目录（如 Download），用户自选位置，系统授权无需权限 */
    private fun exportFile(file: File) {
        pendingMoveFile = file
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = guessMime(file)
            putExtra(Intent.EXTRA_TITLE, file.name)
        }
        try {
            exportLauncher.launch(intent)
        } catch (e: Exception) {
            pendingMoveFile = null
            Toast.makeText(this, "无法打开位置选择器: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    /** 删除 App 内的已下载文件（专属目录，无需权限） */
    private fun confirmDeleteLocal(file: File) {
        AlertDialog.Builder(this)
            .setTitle("删除")
            .setMessage("确定删除《${file.name}》吗？此操作不可恢复。")
            .setPositiveButton("删除") { _, _ ->
                if (file.delete()) {
                    Toast.makeText(this, "已删除", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(this, "删除失败", Toast.LENGTH_SHORT).show()
                }
            }
            .setNegativeButton("取消", null)
            .show()
    }

    /** 用系统文件管理器打开下载目录 */
    private fun openDirWithFileManager() {
        try {
            val uri = FileProvider.getUriForFile(this, "${packageName}.fileprovider", downloadDir)
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "resource/folder")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            // 备选：ACTION_VIEW 无法打开目录时改用 ACTION_GET_CONTENT 展示文件列表
            intent.resolveActivity(packageManager)?.let {
                startActivity(intent)
            } ?: run {
                showFallbackNotice(downloadDir.absolutePath)
            }
        } catch (e: Exception) {
            showFallbackNotice(downloadDir.absolutePath)
        }
    }

    private fun showFallbackNotice(path: String) {
        AlertDialog.Builder(this)
            .setTitle("文件管理器不支持")
            .setMessage("部分手机（华为、小米、OPPO）的文件管理器限制了应用专属目录的访问。\n\n建议使用\"分享\"功能把文件发到微信/QQ，或从文件管理器手动导航到：\n\n$path")
            .setPositiveButton("复制路径") { _, _ -> copyPathToClipboard() }
            .setNegativeButton("关闭", null)
            .show()
    }

    /** 用系统分享菜单一次性把所有下载文件发出去 */
    private fun shareAllFiles(files: List<File>) {
        if (files.isEmpty()) {
            Toast.makeText(this, "还没有下载文件", Toast.LENGTH_SHORT).show()
            return
        }
        val uris = files.mapNotNull { file ->
            try {
                FileProvider.getUriForFile(this, "${packageName}.fileprovider", file)
            } catch (_: Exception) { null }
        }
        if (uris.isEmpty()) {
            Toast.makeText(this, "无法生成分享链接", Toast.LENGTH_SHORT).show()
            return
        }
        val mime = guessMime(files.first())
        val intent = if (uris.size == 1) {
            Intent(Intent.ACTION_SEND).apply {
                type = mime
                putExtra(Intent.EXTRA_STREAM, uris[0])
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
        } else {
            Intent(Intent.ACTION_SEND_MULTIPLE).apply {
                type = "*/*"
                putParcelableArrayListExtra(Intent.EXTRA_STREAM, ArrayList(uris))
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
        }
        startActivity(Intent.createChooser(intent, "分享下载的文件"))
    }

    private fun guessMime(file: File): String {
        return when (file.extension.lowercase()) {
            "jpg", "jpeg" -> "image/jpeg"
            "png" -> "image/png"
            "gif" -> "image/gif"
            "webp" -> "image/webp"
            "mp4" -> "video/mp4"
            "mp3" -> "audio/mpeg"
            "pdf" -> "application/pdf"
            "txt" -> "text/plain"
            else -> "*/*"
        }
    }

    /** 在 App 内部用 WebView 展示已下载文件列表（HTML） */
    private fun showHtmlList(files: List<File>) {
        val html = StringBuilder()
        html.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
        html.append("h2{margin-top:0}table{border-collapse:collapse;width:100%}")
        html.append("td,th{border-bottom:1px solid #333;padding:8px;text-align:left}")
        html.append("body{font-family:sans-serif;padding:16px;background:#111;color:#eee}")
        html.append("</style></head><body><h2>已下载 ${files.size} 个文件</h2>")
        html.append("<table><thead><tr><th>文件名</th><th>大小</th></tr></thead><tbody>")
        files.sortedByDescending { it.lastModified() }.forEach { f ->
            html.append("<tr><td>${f.name}</td><td>${FileItem.formatSize(f.length())}</td></tr>")
        }
        html.append("</tbody></table></body></html>")

        val webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            webViewClient = WebViewClient()
            loadDataWithBaseURL(null, html.toString(), "text/html;charset=utf-8", null, null)
        }
        AlertDialog.Builder(this)
            .setView(webView)
            .setTitle("已下载文件")
            .setPositiveButton("关闭", null)
            .show()
    }

    private fun copyPathToClipboard() {
        val text = "LanTransfer 下载路径: ${downloadDir.absolutePath}"
        val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("LanTransfer", text))
        Toast.makeText(this, "路径已复制", Toast.LENGTH_SHORT).show()
    }

    // --------------------------------------------------------------------------- #
    // SAF 文件解析工具
    // --------------------------------------------------------------------------- #
    private fun getFileName(uri: Uri): String? {
        var result: String? = null
        if (uri.scheme == "content") {
            val cursor = contentResolver.query(uri, null, null, null, null)
            cursor?.use {
                if (it.moveToFirst()) {
                    val idx = it.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                    if (idx >= 0) result = it.getString(idx)
                }
            }
        }
        return result ?: uri.lastPathSegment ?: "file"
    }

    private fun getFileSize(uri: Uri): Long {
        return try {
            contentResolver.query(uri, arrayOf(android.provider.OpenableColumns.SIZE), null, null, null)?.use {
                if (it.moveToFirst()) it.getLong(0) else -1
            } ?: contentResolver.openFileDescriptor(uri, "r")?.use {
                it.statSize
            } ?: -1
        } catch (e: Exception) { -1 }
    }

    override fun onDestroy() {
        super.onDestroy()
        scope.cancel()
        mainHandler.removeCallbacksAndMessages(null)
    }
}
