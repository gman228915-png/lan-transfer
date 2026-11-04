package com.lantransfer

import android.content.Context
import android.util.Base64
import android.util.Log
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.security.KeyFactory
import java.security.KeyStore
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.security.spec.PKCS8EncodedKeySpec
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.KeyManager
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/**
 * 局域网互传 API 客户端（mTLS 双向认证 + TOFU 证书指纹固定）。
 *
 * 安全策略：
 *  1. 客户端证书认证（mTLS）：App 内置客户端证书，服务器只接受持证客户端。
 *     浏览器没有这份证书，TLS 握手阶段即被断开，无法访问任何接口。
 *  2. TOFU 指纹固定：首次连接记录服务器证书 SHA-256 指纹，
 *     之后指纹变化（中间人攻击或电脑端重装证书）立即拒绝连接。
 */
class ApiClient(baseUrl: String, private val token: String, context: Context) {

    companion object {
        private const val TAG = "ApiClient"
        const val ERR_CERT_CHANGED = "CERT_CHANGED"
        private const val KEYSTORE_PASS = "lan-transfer"  // 内存密钥库密码，不落盘
    }

    /** 统一为 https:// 前缀，去掉末尾斜杠 */
    private val baseUrl: String = baseUrl.trim().removeSuffix("/").let {
        if (it.startsWith("http")) it else "https://$it"
    }

    private val pins = context.applicationContext
        .getSharedPreferences("lantransfer_pins", Context.MODE_PRIVATE)
    private val hostKey: String = URL(baseUrl).let { "${it.host}:${it.port}" }

    /** 最近一次失败的错误信息（供界面展示），null 表示无错误 */
    var lastError: String? = null
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
                !saved.equals(fp, ignoreCase = true) ->
                    throw CertificateException(ERR_CERT_CHANGED)
            }
        }
    }

    private fun sha256(bytes: ByteArray): String {
        val md = MessageDigest.getInstance("SHA-256")
        return md.digest(bytes).joinToString(":") { "%02X".format(it) }
    }

    // ------------------------------------------------------------------
    // KeyManager：从 assets 加载内置客户端证书（供服务端 mTLS 校验）
    // ------------------------------------------------------------------
    private fun loadClientKeyManagers(appContext: Context): Array<KeyManager>? {
        return try {
            val certPem = appContext.assets.open("client-cert.pem").use { it.readBytes() }
            val keyPem = appContext.assets.open("client-key.pem").use { it.readBytes() }

            val cert = CertificateFactory.getInstance("X.509")
                .generateCertificate(ByteArrayInputStream(certPem)) as X509Certificate

            val b64 = keyPem.decodeToString()
                .replace("-----BEGIN PRIVATE KEY-----", "")
                .replace("-----END PRIVATE KEY-----", "")
                .replace(Regex("\\s"), "")
            val keyBytes = Base64.decode(b64, Base64.DEFAULT)
            val privateKey = KeyFactory.getInstance("RSA")
                .generatePrivate(PKCS8EncodedKeySpec(keyBytes))

            val ks = KeyStore.getInstance("PKCS12")
            ks.load(null, null)
            ks.setKeyEntry("client", privateKey, KEYSTORE_PASS.toCharArray(), arrayOf(cert))

            val kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
            kmf.init(ks, KEYSTORE_PASS.toCharArray())
            kmf.keyManagers
        } catch (e: Exception) {
            Log.e(TAG, "加载客户端证书失败", e)
            null
        }
    }

    private val clientKeyManagers: Array<KeyManager>? =
        loadClientKeyManagers(context.applicationContext)

    private val sslContext: SSLContext = SSLContext.getInstance("TLS").apply {
        init(clientKeyManagers, arrayOf<TrustManager>(PinnedTrustManager()), SecureRandom())
    }

    // ------------------------------------------------------------------
    // 连接工厂
    // ------------------------------------------------------------------
    /** 打开连接；HTTPS 时应用 mTLS + 指纹固定。主机名校验由指纹固定替代（证书无 IP SAN）。 */
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
                lastError = "服务器返回 ${conn.responseCode}"
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

    fun uploadFile(name: String, inputStream: InputStream, size: Long): Boolean {
        val encoded = URLEncoder.encode(name, "UTF-8")
        val conn = open(buildUrl("api/upload", "name=$encoded"))
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.connectTimeout = 30000
        conn.readTimeout = 30000
        conn.setRequestProperty("Content-Type", "application/octet-stream")
        conn.setRequestProperty("Content-Length", size.toString())
        lastError = null

        try {
            inputStream.use { stream ->
                conn.outputStream.use { os ->
                    val buf = ByteArray(65536)
                    var remaining = size
                    while (remaining > 0) {
                        val read = stream.read(buf, 0, minOf(buf.size, remaining.toInt()))
                        if (read <= 0) break
                        os.write(buf, 0, read)
                        remaining -= read
                    }
                }
            }
            if (conn.responseCode != 200) {
                lastError = "服务器返回 ${conn.responseCode}"
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

    fun deleteFile(name: String): Boolean {
        val encoded = URLEncoder.encode(name, "UTF-8")
        val conn = open(buildUrl("api/files", "name=$encoded"))
        conn.requestMethod = "DELETE"
        conn.connectTimeout = 8000
        conn.readTimeout = 8000
        lastError = null

        try {
            if (conn.responseCode != 200) {
                lastError = "服务器返回 ${conn.responseCode}"
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

    /** 打开下载连接（由调用方读取 inputStream 并 disconnect）。 */
    fun openDownload(name: String): HttpURLConnection {
        val encoded = URLEncoder.encode(name, "UTF-8")
        val conn = open(buildUrl("download", "name=$encoded"))
        conn.requestMethod = "GET"
        conn.connectTimeout = 15000
        conn.readTimeout = 60000
        return conn
    }

    fun testConnection(): Boolean {
        return try {
            lastError = null
            val conn = open(buildUrl("api/info"))
            conn.requestMethod = "GET"
            conn.connectTimeout = 5000
            conn.readTimeout = 5000
            val ok = conn.responseCode == 200
            if (!ok) lastError = "服务器返回 ${conn.responseCode}"
            conn.disconnect()
            ok
        } catch (e: Exception) {
            lastError = errorText(e)
            Log.e(TAG, "testConnection failed", e)
            false
        }
    }

    // ------------------------------------------------------------------
    // 错误翻译
    // ------------------------------------------------------------------
    fun errorText(e: Throwable): String {
        val msg = e.message ?: ""
        val cause = e.cause?.message ?: ""
        return when {
            msg.contains(ERR_CERT_CHANGED) ->
                "电脑端证书已更换（可能是重装了程序）。请在 App 设置里清除本 App 数据后重试。"
            msg.contains("certificate") || cause.contains("certificate") ||
                msg.contains("handshake") || cause.contains("handshake") ->
                "电脑端要求客户端证书（mTLS），但 App 证书加载失败。"
            msg.contains("ConnectException") || msg.contains("SocketTimeout") ||
                cause.contains("timeout") ->
                "无法连接，请确认手机和电脑在同一 Wi-Fi，且电脑端程序正在运行。"
            msg.contains("UnknownHost") -> "地址格式错误，应为 https://IP:端口"
            else -> msg.ifBlank { cause.ifBlank { e.javaClass.simpleName } }
        }
    }
}
