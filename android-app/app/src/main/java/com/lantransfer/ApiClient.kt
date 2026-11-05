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
    fun listFiles(): List<FileItem> {
        val conn = open(buildUrl("api/files"))
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
                    kind = o.getString("kind")
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

    /**
     * 上传文件，onProgress(sentBytes, totalBytes) 会随上传进度回调。
     */
    fun uploadFile(
        name: String,
        inputStream: InputStream,
        size: Long,
        onProgress: (Long, Long) -> Unit = { _, _ -> }
    ): Boolean {
        lastError = null
        pinMismatch = false
        if (size <= 0) {
            lastError = "无法读取文件大小，请换一个文件重试"
            return false
        }
        val encoded = URLEncoder.encode(name, "UTF-8")
        val conn = open(buildUrl("api/upload", "name=$encoded"))
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
     * 失败时删除未完成的文件，方便直接重试。
     */
    fun downloadFile(
        name: String,
        dest: File,
        onProgress: (Long, Long) -> Unit = { _, _ -> }
    ): Boolean {
        lastError = null
        pinMismatch = false
        val encoded = URLEncoder.encode(name, "UTF-8")
        val conn = open(buildUrl("download", "name=$encoded"))
        conn.requestMethod = "GET"
        conn.connectTimeout = 15000
        conn.readTimeout = 60000

        return try {
            if (conn.responseCode != 200) {
                lastError = httpError(conn.responseCode)
                return false
            }
            val total = conn.contentLengthLong.takeIf { it > 0 } ?: -1L
            var done = 0L
            conn.inputStream.use { input ->
                FileOutputStream(dest).use { os ->
                    val buf = ByteArray(65536)
                    onProgress(0L, total)
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
                dest.delete()
                lastError = "下载中断（$done / $total 字节），可点重试"
                false
            } else {
                true
            }
        } catch (e: Exception) {
            dest.delete()
            lastError = errorText(e)
            Log.e(TAG, "download failed", e)
            false
        } finally {
            conn.disconnect()
        }
    }

    fun deleteFile(name: String): Boolean {
        val encoded = URLEncoder.encode(name, "UTF-8")
        val conn = open(buildUrl("api/files", "name=$encoded"))
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
