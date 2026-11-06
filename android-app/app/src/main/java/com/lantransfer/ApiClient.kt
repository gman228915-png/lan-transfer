package com.lantransfer

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/**
 * 局域网互传 API 客户端（HTTPS + TOFU 证书指纹固定）。
 *
 * 安全策略：
 *  1. 只允许 HTTPS，口令随每次请求携带，服务端校验。
 *  2. TOFU 指纹固定：首次连接记录服务器证书 SHA-256 指纹，
 *     之后指纹变化（中间人攻击或电脑端证书重建）立即拒绝连接，
 *     用户可在界面上确认后清除旧指纹重连。
 */
data class SpaceInfo(val name: String, val mode: String, val description: String)

class ApiClient(baseUrl: String, private val token: String, context: Context) {

    companion object {
        private const val TAG = "ApiClient"
        const val ERR_CERT_CHANGED = "CERT_CHANGED"
    }

    /** 统一为 https:// 前缀，去掉末尾斜杠 */
    private val baseUrl: String = baseUrl.trim().removeSuffix("/").let {
        when {
            it.startsWith("https://") -> it
            it.startsWith("http://") -> "https://" + it.removePrefix("http://")
            else -> "https://$it"
        }
    }

    private val pins = context.applicationContext
        .getSharedPreferences("lantransfer_pins", Context.MODE_PRIVATE)
    private val hostKey: String = URL(baseUrl).let { "${it.host}:${it.port}" }

    /** 最近一次失败的错误信息（供界面展示），null 表示无错误 */
    var lastError: String? = null
        private set

    /** 最近一次失败是否由证书指纹变化引起 */
    var pinMismatch: Boolean = false
        private set

    // ------------------------------------------------------------------
    // TrustManager：TOFU 证书指纹固定
    // ------------------------------------------------------------------
    private inner class PinnedTrustManager : X509TrustManager {
        override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}

        override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
            val leaf = chain.firstOrNull()
                ?: throw CertificateException("服务器未提供证书")
            val fp = sha256(leaf.encoded)
            val saved = pins.getString(hostKey, null)
            when {
                saved == null -> {
                    pins.edit().putString(hostKey, fp).apply()
                    Log.i(TAG, "已固定 $hostKey 证书指纹: $fp")
                }
                !saved.equals(fp, ignoreCase = true) -> {
                    pinMismatch = true
                    throw CertificateException(ERR_CERT_CHANGED)
                }
            }
        }
    }

    private fun sha256(bytes: ByteArray): String {
        val md = MessageDigest.getInstance("SHA-256")
        return md.digest(bytes).joinToString(":") { "%02X".format(it) }
    }

    /** 清除本机的证书指纹记录（电脑端证书重建后使用） */
    fun resetPin() {
        pins.edit().remove(hostKey).apply()
        pinMismatch = false
    }

    private val sslContext: SSLContext = SSLContext.getInstance("TLS").apply {
        init(null, arrayOf<TrustManager>(PinnedTrustManager()), SecureRandom())
    }

    // ------------------------------------------------------------------
    // 连接工厂
    // ------------------------------------------------------------------
    /** 打开连接；HTTPS 时应用指纹固定。主机名校验由指纹固定替代（证书无 IP SAN）。 */
    fun open(url: String): HttpURLConnection {
        val conn = URL(url).openConnection() as HttpURLConnection
        if (conn is HttpsURLConnection) {
            conn.sslSocketFactory = sslContext.socketFactory
            conn.hostnameVerifier = HostnameVerifier { _, _ -> true }
        }
        return conn
    }

    private fun buildUrl(path: String, extraQuery: String? = null): String {
        val sep = if (baseUrl.endsWith("/")) "" else "/"
        var url = "${baseUrl}${sep}${path}?k=$token"
        if (!extraQuery.isNullOrBlank()) url += "&$extraQuery"
        return url
    }

    // ------------------------------------------------------------------
    // 业务 API
    // ------------------------------------------------------------------
    fun listFiles(space: String? = null): List<FileItem> {
        val extra = if (space.isNullOrBlank()) null
        else "space=" + URLEncoder.encode(space, "UTF-8")
        val conn = open(buildUrl("api/files", extra))
        conn.requestMethod = "GET"
        conn.connectTimeout = 8000
        conn.readTimeout = 8000
        conn.setRequestProperty("Cache-Control", "no-store")
        lastError = null

        try {
            if (conn.responseCode != 200) {
                lastError = httpError(conn.responseCode)
                return emptyList()
            }
            val body = conn.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(body)
            val arr = json.optJSONArray("files") ?: return emptyList()
            val result = mutableListOf<FileItem>()
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                result.add(FileItem(
                    name = o.getString("name"),
                    size = o.getLong("size"),
                    mtime = o.getLong("mtime"),
                    kind = o.getString("kind"),
                    space = o.optString("space", "")
                ))
            }
            return result
        } catch (e: Exception) {
            lastError = errorText(e)
            Log.e(TAG, "listFiles failed", e)
            return emptyList()
        } finally {
            conn.disconnect()
        }
    }

    /** 拉取空间列表（名称 / 读写模式 / 说明）。 */
    fun listSpaces(): List<SpaceInfo> {
        val conn = open(buildUrl("api/spaces"))
        conn.requestMethod = "GET"
        conn.connectTimeout = 8000
        conn.readTimeout = 8000
        lastError = null
        return try {
            if (conn.responseCode != 200) {
                lastError = httpError(conn.responseCode)
                return emptyList()
            }
            val body = conn.inputStream.bufferedReader().use { it.readText() }
            val arr = JSONObject(body).optJSONArray("spaces") ?: return emptyList()
            val result = mutableListOf<SpaceInfo>()
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                result.add(SpaceInfo(
                    name = o.optString("name", ""),
                    mode = o.optString("mode", "rw"),
                    description = o.optString("description", "")
                ))
            }
            result
        } catch (e: Exception) {
            lastError = errorText(e)
            Log.e(TAG, "listSpaces failed", e)
            emptyList()
        } finally {
            conn.disconnect()
        }
    }

    /**
     * 上传文件，onProgress(sentBytes, totalBytes) 会随上传进度回调。
     * space 为空时使用服务端默认空间（保持旧行为）。
     */
    fun uploadFile(
        name: String,
        inputStream: InputStream,
        size: Long,
        space: String? = null,
        onProgress: (Long, Long) -> Unit = { _, _ -> }
    ): Boolean {
        lastError = null
        pinMismatch = false
        if (size <= 0) {
            lastError = "无法读取文件大小，请换一个文件重试"
            return false
        }
        val encoded = URLEncoder.encode(name, "UTF-8")
        val query = "name=$encoded" + if (space.isNullOrBlank()) ""
        else "&space=" + URLEncoder.encode(space, "UTF-8")
        val conn = open(buildUrl("api/upload", query))
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.connectTimeout = 30000
        conn.readTimeout = 60000
        conn.setRequestProperty("Content-Type", "application/octet-stream")
        conn.setRequestProperty("Content-Length", size.toString())

        try {
            inputStream.use { stream ->
                conn.outputStream.use { os ->
                    val buf = ByteArray(65536)
                    var sent = 0L
                    onProgress(0L, size)
                    while (sent < size) {
                        val read = stream.read(buf, 0, minOf(buf.size.toLong(), size - sent).toInt())
                        if (read <= 0) break
                        os.write(buf, 0, read)
                        sent += read
                        onProgress(sent, size)
                    }
                    if (sent < size) {
                        lastError = "文件读取中断（已发送 $sent / $size 字节）"
                        return false
                    }
                }
            }
            if (conn.responseCode != 200) {
                lastError = httpError(conn.responseCode)
                return false
            }
            val body = conn.inputStream.bufferedReader().use { it.readText() }
            return JSONObject(body).optBoolean("ok", false)
        } catch (e: Exception) {
            lastError = errorText(e)
            Log.e(TAG, "upload failed", e)
            return false
        } finally {
            conn.disconnect()
        }
    }

    /**
     * 下载文件到 dest，onProgress(downloadedBytes, totalBytes) 随进度回调。
     * 使用 dest.part 作为临时文件：失败时保留，重试时用 Range 从断点继续。
     */
    fun downloadFile(
        name: String,
        dest: File,
        space: String? = null,
        onProgress: (Long, Long) -> Unit = { _, _ -> }
    ): Boolean {
        lastError = null
        pinMismatch = false
        val encoded = URLEncoder.encode(name, "UTF-8")
        val part = File(dest.parentFile, dest.name + ".part")
        var offset = if (part.exists()) part.length() else 0L
        val query = "name=$encoded" + if (space.isNullOrBlank()) ""
        else "&space=" + URLEncoder.encode(space, "UTF-8")
        val conn = open(buildUrl("download", query))
        conn.requestMethod = "GET"
        conn.connectTimeout = 15000
        conn.readTimeout = 60000
        if (offset > 0) conn.setRequestProperty("Range", "bytes=$offset-")

        return try {
            val code = conn.responseCode
            if (code != 200 && code != 206) {
                lastError = httpError(code)
                return false
            }
            if (offset > 0 && code == 200) {
                // 服务端没接受 Range（文件已变化），从头下载
                part.delete()
                offset = 0L
            }
            var total = -1L
            if (code == 206) {
                val header = conn.getHeaderField("Content-Range") ?: ""
                val start = header.substringAfter("bytes ", "")
                    .substringBefore("-").toLongOrNull()
                val remoteTotal = header.substringAfter("/", "").toLongOrNull()
                if (start == null || start != offset) {
                    part.delete()
                    lastError = "断点与服务器文件不匹配，已重新开始，请重试"
                    return false
                }
                total = remoteTotal ?: -1L
            } else {
                total = conn.contentLengthLong.takeIf { it > 0 }?.plus(offset) ?: -1L
            }

            var done = offset
            conn.inputStream.use { input ->
                FileOutputStream(part, offset > 0).use { os ->
                    val buf = ByteArray(65536)
                    onProgress(done, total)
                    while (true) {
                        val read = input.read(buf)
                        if (read <= 0) break
                        os.write(buf, 0, read)
                        done += read
                        onProgress(done, total)
                    }
                }
            }
            if (total > 0 && done < total) {
                lastError = "下载中断（已保留 ${FileItem.formatSize(done)} / " +
                    "${FileItem.formatSize(total)}），点重试可继续"
                false
            } else {
                if (dest.exists()) dest.delete()
                if (part.renameTo(dest)) {
                    true
                } else {
                    part.copyTo(dest, overwrite = true)
                    part.delete()
                    true
                }
            }
        } catch (e: Exception) {
            // 保留 part 文件，下一次点击重试时从断点继续
            lastError = errorText(e) + "（已保留下载断点，可重试续传）"
            Log.e(TAG, "download failed", e)
            false
        } finally {
            conn.disconnect()
        }
    }

    fun deleteFile(name: String, space: String? = null): Boolean {
        val encoded = URLEncoder.encode(name, "UTF-8")
        val query = "name=$encoded" + if (space.isNullOrBlank()) ""
        else "&space=" + URLEncoder.encode(space, "UTF-8")
        val conn = open(buildUrl("api/files", query))
        conn.requestMethod = "DELETE"
        conn.connectTimeout = 8000
        conn.readTimeout = 8000
        lastError = null

        try {
            if (conn.responseCode != 200) {
                lastError = httpError(conn.responseCode)
                return false
            }
            val body = conn.inputStream.bufferedReader().use { it.readText() }
            return JSONObject(body).optBoolean("ok", false)
        } catch (e: Exception) {
            lastError = errorText(e)
            Log.e(TAG, "delete failed", e)
            return false
        } finally {
            conn.disconnect()
        }
    }

    fun testConnection(): Boolean {
        lastError = null
        pinMismatch = false
        return try {
            val conn = open(buildUrl("api/info"))
            conn.requestMethod = "GET"
            conn.connectTimeout = 5000
            conn.readTimeout = 5000
            val code = conn.responseCode
            if (code != 200) lastError = httpError(code)
            conn.disconnect()
            code == 200
        } catch (e: Exception) {
            lastError = errorText(e)
            Log.e(TAG, "testConnection failed", e)
            false
        }
    }

    // ------------------------------------------------------------------
    // 错误翻译
    // ------------------------------------------------------------------
    private fun httpError(code: Int): String = when (code) {
        401, 403 -> "口令错误或已失效，请重新扫描电脑端二维码"
        404 -> "服务器上找不到这个文件"
        in 500..599 -> "电脑端处理失败（HTTP $code），请稍后重试"
        else -> "服务器返回 HTTP $code"
    }

    fun errorText(e: Throwable): String {
        val msg = (e.message ?: "") + " " + (e.cause?.message ?: "")
        return when {
            msg.contains(ERR_CERT_CHANGED) ->
                "电脑端的证书指纹变了（可能重装过程序）。确认是你自己的电脑后，点“清除并重连”。"
            msg.contains("CLEARTEXT") || msg.contains("cleartext") ->
                "电脑端没有启用 HTTPS，请在电脑端确认加密传输已开启后重试。"
            msg.contains("certificate") || msg.contains("handshake") ->
                "HTTPS 握手失败，请确认电脑端程序正在运行。"
            msg.contains("ConnectException") || msg.contains("SocketTimeout") ||
                msg.contains("timeout") || msg.contains("Failed to connect") ->
                "连不上，请确认手机和电脑在同一个 Wi-Fi，且电脑端程序已启动、防火墙允许访问。"
            msg.contains("UnknownHost") -> "地址格式错误，应形如 https://192.168.1.10:8765"
            else -> msg.trim().ifBlank { e.javaClass.simpleName }
        }
    }
}
