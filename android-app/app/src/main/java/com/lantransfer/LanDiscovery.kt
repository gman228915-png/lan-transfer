package com.lantransfer

import android.content.Context
import android.net.wifi.WifiManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.SocketTimeoutException

/** 局域网里发现的一个 lan-transfer 服务 */
data class DiscoveredService(
    val server: String,
    val ip: String,
    val port: Int,
    val version: String,
    val spaces: Int,
    val host: String
)

/**
 * 监听电脑端周期性发出的 UDP beacon（默认 8766），
 * 免去手输 IP：连上同一个 Wi-Fi 就能看到可用设备。
 */
class LanDiscovery(private val context: Context, private val port: Int = 8766) {

    suspend fun scan(timeoutMs: Long = 4000): List<DiscoveredService> =
        withContext(Dispatchers.IO) {
            val lock = acquireMulticastLock()
            val socket = DatagramSocket(null)
            val found = LinkedHashMap<String, DiscoveredService>()
            try {
                socket.reuseAddress = true
                socket.broadcast = true
                socket.soTimeout = 500
                socket.bind(InetSocketAddress(port))
                val deadline = System.currentTimeMillis() + timeoutMs
                val buffer = ByteArray(4096)
                while (System.currentTimeMillis() < deadline) {
                    try {
                        val packet = DatagramPacket(buffer, buffer.size)
                        socket.receive(packet)
                        val text = String(packet.data, 0, packet.length, Charsets.UTF_8)
                        val obj = runCatching { JSONObject(text) }.getOrNull() ?: continue
                        if (obj.optString("app") != "lan-transfer") continue
                        val servicePort = obj.optInt("port", 0)
                        if (servicePort <= 0) continue
                        val scheme = obj.optString("scheme", "https").ifEmpty { "https" }
                        // beacon 里的 ip 是电脑的局域网地址，比广播源地址更可靠
                        val ip = obj.optString("ip", "")
                            .ifEmpty { packet.address?.hostAddress ?: "" }
                        if (ip.isEmpty()) continue
                        val key = "$ip:$servicePort"
                        if (found.containsKey(key)) continue
                        found[key] = DiscoveredService(
                            server = "$scheme://$ip:$servicePort",
                            ip = ip,
                            port = servicePort,
                            version = obj.optString("version", ""),
                            spaces = obj.optInt("spaces", 0),
                            host = obj.optString("host", "")
                        )
                    } catch (_: SocketTimeoutException) {
                        // 半秒没包就继续等，直到总时长结束
                    } catch (_: Exception) {
                        // 忽略格式不对的广播
                    }
                }
            } finally {
                runCatching { socket.close() }
                releaseMulticastLock(lock)
            }
            found.values.toList()
        }

    private fun acquireMulticastLock(): WifiManager.MulticastLock? = try {
        val wifi = context.applicationContext
            .getSystemService(Context.WIFI_SERVICE) as? WifiManager
        wifi?.createMulticastLock("lan-transfer-discovery")?.apply {
            setReferenceCounted(false)
            acquire()
        }
    } catch (_: Exception) {
        null
    }

    private fun releaseMulticastLock(lock: WifiManager.MulticastLock?) {
        try {
            lock?.release()
        } catch (_: Exception) {
        }
    }
}
