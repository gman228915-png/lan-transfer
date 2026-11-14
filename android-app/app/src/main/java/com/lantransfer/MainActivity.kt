package com.lantransfer

import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.RadioGroup
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import androidx.core.os.LocaleListCompat
import androidx.core.content.FileProvider
import androidx.recyclerview.widget.LinearLayoutManager
import com.google.zxing.integration.android.IntentIntegrator
import com.lantransfer.databinding.ActivityMainBinding
import kotlinx.coroutines.*
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.max

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private var api: ApiClient? = null
    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private lateinit var adapter: FileAdapter
    private val mainHandler = Handler(Looper.getMainLooper())

    /** 外观：自定义背景（只存本机 Uri，不参与传输逻辑） */
    private val appearancePrefs by lazy { getSharedPreferences("appearance", MODE_PRIVATE) }
    private val pickBackground = registerForActivityResult(
        ActivityResultContracts.OpenDocument()
    ) { uri: Uri? ->
        if (uri != null) {
            try {
                contentResolver.takePersistableUriPermission(
                    uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
            } catch (_: SecurityException) {
                // 某些文件管理器不支持持久授权，本次仍可用
            }
            appearancePrefs.edit().putString("bg_uri", uri.toString()).apply()
            applyBackground()
            Toast.makeText(this, getString(R.string.toast_bg_updated), Toast.LENGTH_SHORT).show()
        }
    }

    /** 正在连接 / 上传 / 下载，防止重复操作 */
    private var opRunning = false
    private var lastProgressAt = 0L
    private var lastRateAt = 0L
    private var lastRateDone = 0L

    /** 空间选择：空字符串表示「全部空间」（保持旧行为） */
    private var currentSpace = ""
    private var currentSpaceReadOnly = false
    private var spaceList: List<SpaceInfo> = emptyList()
    private val spaceNames = mutableListOf(getString(R.string.space_all))
    private lateinit var spaceAdapter: ArrayAdapter<String>

    /** 局域网 UDP 自动发现的设备 */
    private val deviceList = mutableListOf<DiscoveredService>()
    private val deviceNames = mutableListOf<String>()
    private lateinit var deviceAdapter: ArrayAdapter<String>
    private var discovering = false
    private val discovery by lazy { LanDiscovery(this) }

    /** 同时上传的文件数上限，避免一次选几十个把设备拖死 */
    private val uploadConcurrency = 2

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

        spaceAdapter = ArrayAdapter(this, R.layout.item_spinner, spaceNames)
        spaceAdapter.setDropDownViewResource(R.layout.item_spinner)
        binding.spSpace.adapter = spaceAdapter
        binding.spSpace.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?,
                                        position: Int, id: Long) {
                applySpaceSelection(position, fromUser = true)
            }

            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        deviceAdapter = ArrayAdapter(this, R.layout.item_spinner, deviceNames)
        deviceAdapter.setDropDownViewResource(R.layout.item_spinner)
        binding.spDevices.adapter = deviceAdapter
        binding.spDevices.isEnabled = false
        binding.spDevices.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?,
                                        position: Int, id: Long) {
                val device = deviceList.getOrNull(position) ?: return
                binding.etUrl.setText(device.server)
                // 已经有口令时，点选设备即可直接连接
                if (binding.etToken.text.toString().isNotBlank() && !opRunning) connect()
            }

            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        binding.btnConnect.setOnClickListener { connect() }
        binding.btnUpload.setOnClickListener { pickFile() }
        binding.btnScan.setOnClickListener { scan() }
        binding.btnDiscover.setOnClickListener { startDiscovery(false) }
        binding.btnOpenDownloads.setOnClickListener { showDownloadOptions() }
        binding.btnAppearance.setOnClickListener { showAppearanceDialog() }
        binding.btnNewSpace.setOnClickListener { showNewSpaceDialog() }
        applyBackground()

        // 记住上次地址与口令；有记录时自动重连
        val sp = getSharedPreferences("lantransfer", MODE_PRIVATE)
        val savedUrl = sp.getString("url", "") ?: ""
        val savedToken = sp.getString("token", "") ?: ""
        if (savedUrl.isNotEmpty()) binding.etUrl.setText(savedUrl)
        if (savedToken.isNotEmpty()) binding.etToken.setText(savedToken)
        if (savedUrl.isNotEmpty() && savedToken.isNotEmpty()) {
            binding.tvStatus.text = getString(R.string.status_reconnecting)
            connect()
        } else {
            binding.tvStatus.text = getString(R.string.status_not_connected)
        }

        // 没有历史地址时，连上 Wi-Fi 后自动扫描局域网设备
        if (savedUrl.isEmpty()) startDiscovery(true)
    }

    // --------------------------------------------------------------------------- #
    // 外观：自定义背景（只影响界面，不影响传输）
    // --------------------------------------------------------------------------- #
    private fun showAppearanceDialog() {
        val items = arrayOf(getString(R.string.dlg_pick_bg), getString(R.string.dlg_reset_bg), getString(R.string.dlg_language))
        AlertDialog.Builder(this)
            .setTitle(R.string.dlg_appearance_title)
            .setItems(items) { _, which ->
                when (which) {
                    0 -> pickBackground.launch(arrayOf("image/*"))
                    1 -> {
                        appearancePrefs.edit().remove("bg_uri").apply()
                        applyBackground()
                        Toast.makeText(this, getString(R.string.toast_bg_reset), Toast.LENGTH_SHORT).show()
                    }
                    2 -> showLanguageDialog()
                }
            }
            .show()
    }

    /** 手动覆盖语言（AppCompatDelegate 持久化；空标签 = 跟随系统） */
    private fun showLanguageDialog() {
        val tags = arrayOf("", "zh-CN", "zh-TW", "en", "ja", "ko", "es", "ru")
        val labels = arrayOf(
            getString(R.string.language_follow_system),
            "简体中文", "繁體中文", "English", "日本語", "한국어", "Español", "Русский"
        )
        val current = AppCompatDelegate.getApplicationLocales().toLanguageTags()
        val checked = tags.indexOfFirst {
            it.isNotEmpty() && current.lowercase().startsWith(it.lowercase())
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.dlg_language)
            .setSingleChoiceItems(labels, if (checked >= 0) checked else 0) { dialog, which ->
                if (tags[which].isEmpty()) {
                    AppCompatDelegate.setApplicationLocales(LocaleListCompat.getEmptyLocaleList())
                } else {
                    AppCompatDelegate.setApplicationLocales(
                        LocaleListCompat.forLanguageTags(tags[which]))
                }
                dialog.dismiss()
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun applyBackground() {
        val uriString = appearancePrefs.getString("bg_uri", null)
        if (uriString.isNullOrEmpty()) {
            binding.imgBackground.setImageDrawable(null)
            binding.bgDim.alpha = 0f
            return
        }
        binding.bgDim.alpha = 0.45f
        val uri = Uri.parse(uriString)
        Thread {
            try {
                val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                contentResolver.openInputStream(uri)?.use {
                    BitmapFactory.decodeStream(it, null, bounds)
                }
                var sample = 1
                while (bounds.outWidth > 0 && bounds.outWidth / (sample * 2) >= 900) {
                    sample *= 2
                }
                val opts = BitmapFactory.Options().apply { inSampleSize = sample }
                val bitmap = contentResolver.openInputStream(uri)?.use {
                    BitmapFactory.decodeStream(it, null, opts)
                }
                mainHandler.post {
                    if (!isFinishing && !isDestroyed) binding.imgBackground.setImageBitmap(bitmap)
                }
            } catch (_: Exception) {
                mainHandler.post {
                    if (!isFinishing && !isDestroyed) {
                        binding.imgBackground.setImageDrawable(null)
                        binding.bgDim.alpha = 0f
                        Toast.makeText(this, getString(R.string.toast_bg_invalid),
                            Toast.LENGTH_SHORT).show()
                    }
                }
            }
        }.start()
    }

    // --------------------------------------------------------------------------- #
    // 局域网 UDP 自动发现
    // --------------------------------------------------------------------------- #
    private fun startDiscovery(auto: Boolean) {
        if (discovering) return
        discovering = true
        binding.btnDiscover.isEnabled = false
        binding.btnDiscover.text = getString(R.string.btn_scanning)
        binding.tvDiscoverHint.text =
            if (auto) getString(R.string.discover_scanning_auto) else getString(R.string.discover_scanning)
        scope.launch {
            val list = discovery.scan(if (auto) 5000L else 4000L)
            withContext(Dispatchers.Main) {
                discovering = false
                binding.btnDiscover.isEnabled = true
                binding.btnDiscover.text = getString(R.string.btn_scan)
                applyDevices(list, auto)
            }
        }
    }

    private fun applyDevices(list: List<DiscoveredService>, auto: Boolean) {
        deviceList.clear()
        deviceList.addAll(list)
        deviceNames.clear()
        if (list.isEmpty()) {
            deviceNames.add(getString(R.string.device_none))
        } else {
            for (item in list) {
                deviceNames.add("${item.ip}:${item.port}  v${item.version.ifEmpty { "?" }}")
            }
        }
        deviceAdapter.notifyDataSetChanged()
        binding.spDevices.isEnabled = list.isNotEmpty()
        if (list.isNotEmpty()) {
            binding.spDevices.setSelection(0)
            binding.etUrl.setText(list[0].server)
            binding.tvDiscoverHint.text = getString(R.string.discover_found_hint, list.size)
            if (auto) {
                Toast.makeText(this, getString(R.string.discover_found_toast, list.size),
                    Toast.LENGTH_SHORT).show()
            }
        } else {
            binding.tvDiscoverHint.text = if (auto) {
                getString(R.string.discover_empty_auto)
            } else {
                getString(R.string.discover_empty_manual)
            }
            if (!auto) {
                Toast.makeText(this, getString(R.string.discover_empty_toast),
                    Toast.LENGTH_SHORT).show()
            }
        }
    }

    // --------------------------------------------------------------------------- #
    // 扫码
    // --------------------------------------------------------------------------- #
    private fun scan() {
        IntentIntegrator(this)
            .setPrompt(getString(R.string.scan_prompt))
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
            Toast.makeText(this, getString(R.string.scan_bad_qr), Toast.LENGTH_SHORT).show()
            return
        }
        val scheme = if (uri.scheme?.lowercase() == "http") "http" else "https"
        val port = if (uri.port > 0) uri.port else 8765
        val token = uri.getQueryParameter("k") ?: ""
        val base = "$scheme://$host:$port"
        binding.etUrl.setText(base)
        binding.etToken.setText(token)
        if (token.isEmpty()) {
            Toast.makeText(this, getString(R.string.scan_no_token), Toast.LENGTH_LONG).show()
            return
        }
        Toast.makeText(this, getString(R.string.scan_ok, base), Toast.LENGTH_SHORT).show()
        connect()
    }

    // --------------------------------------------------------------------------- #
    // 连接
    // --------------------------------------------------------------------------- #
    private fun connect() {
        if (opRunning) {
            Toast.makeText(this, getString(R.string.busy), Toast.LENGTH_SHORT).show()
            return
        }
        val url = binding.etUrl.text.toString().trim().trimEnd('/')
        var token = binding.etToken.text.toString().trim()

        if (url.isEmpty()) {
            Toast.makeText(this, getString(R.string.err_need_url),
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
            Toast.makeText(this, getString(R.string.err_need_token), Toast.LENGTH_SHORT).show()
            return
        }

        val api = ApiClient(url, token, this)
        binding.tvStatus.text = getString(R.string.status_connecting)

        scope.launch {
            val ok = api.testConnection()
            withContext(Dispatchers.Main) {
                if (ok) {
                    this@MainActivity.api = api
                    getSharedPreferences("lantransfer", MODE_PRIVATE)
                        .edit().putString("url", url).putString("token", token).apply()
                    binding.tvStatus.text = getString(R.string.status_connected)
                    currentSpace = ""
                    currentSpaceReadOnly = false
                    spaceList = emptyList()
                    spaceNames.clear()
                    spaceNames.add(getString(R.string.space_all))
                    spaceAdapter.notifyDataSetChanged()
                    loadSpaces()
                } else {
                    val msg = api.lastError ?: getString(R.string.err_connect)
                    binding.tvStatus.text = getString(R.string.status_failed)
                    if (api.pinMismatch) {
                        AlertDialog.Builder(this@MainActivity)
                            .setTitle(R.string.pin_title)
                            .setMessage(msg)
                            .setPositiveButton(R.string.pin_clear_reconnect) { _, _ ->
                                api.resetPin()
                                connect()
                            }
                            .setNegativeButton(getString(R.string.cancel), null)
                            .show()
                    } else {
                        Toast.makeText(this@MainActivity, msg, Toast.LENGTH_LONG).show()
                    }
                }
            }
        }
    }

    private fun loadSpaces() {
        val api = api ?: return
        scope.launch {
            val spaces = api.listSpaces()
            withContext(Dispatchers.Main) {
                applySpaces(spaces)
                refreshFiles()
            }
        }
    }

    private fun applySpaces(spaces: List<SpaceInfo>) {
        spaceList = spaces
        spaceNames.clear()
        spaceNames.add(getString(R.string.space_all))
        for (space in spaces) {
            spaceNames.add(if (space.mode == "ro") "${space.name}${getString(R.string.space_ro_suffix)}" else space.name)
        }
        spaceAdapter.notifyDataSetChanged()
        val index = spaces.indexOfFirst { it.name == currentSpace }
        val target = if (index >= 0) index + 1 else 0
        if (binding.spSpace.selectedItemPosition == target) {
            applySpaceSelection(target, fromUser = false)
        } else {
            binding.spSpace.setSelection(target)
        }
    }

    private fun applySpaceSelection(position: Int, fromUser: Boolean) {
        if (position <= 0 || position > spaceList.size) {
            currentSpace = ""
            currentSpaceReadOnly = false
        } else {
            val space = spaceList[position - 1]
            currentSpace = space.name
            currentSpaceReadOnly = space.mode == "ro"
        }
        binding.btnUpload.isEnabled = !currentSpaceReadOnly && !opRunning
        if (fromUser && currentSpaceReadOnly) {
            Toast.makeText(this, getString(R.string.space_readonly_toast, currentSpace), Toast.LENGTH_SHORT).show()
        }
        if (api != null) refreshFiles()
    }

    // --------------------------------------------------------------------------- #
    // 新建分区：目录建在电脑上，手机只把路径发给服务端（不使用 SAF 选手机目录）
    // --------------------------------------------------------------------------- #
    private fun showNewSpaceDialog() {
        if (api == null) {
            Toast.makeText(this, getString(R.string.err_no_server), Toast.LENGTH_SHORT).show()
            return
        }
        val view = layoutInflater.inflate(R.layout.dialog_new_space, null)
        val etName = view.findViewById<EditText>(R.id.etSpaceName)
        val etPath = view.findViewById<EditText>(R.id.etSpacePath)
        val rgMode = view.findViewById<RadioGroup>(R.id.rgSpaceMode)
        val etDesc = view.findViewById<EditText>(R.id.etSpaceDesc)
        val tvError = view.findViewById<TextView>(R.id.tvSpaceError)
        view.findViewById<Button>(R.id.btnBrowseSpace).setOnClickListener {
            showBrowseDialog(etPath.text.toString().trim(), etPath)
        }

        val dialog = AlertDialog.Builder(this)
            .setTitle(R.string.dlg_new_space_title)
            .setView(view)
            .setPositiveButton(R.string.btn_create, null)
            .setNegativeButton(R.string.cancel, null)
            .create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                val name = etName.text.toString().trim()
                val path = etPath.text.toString().trim()
                val mode = if (rgMode.checkedRadioButtonId == R.id.rbSpaceReadOnly) "ro" else "rw"
                submitNewSpace(dialog, name, path, mode, etDesc.text.toString().trim(), tvError)
            }
        }
        dialog.show()
    }

    private fun submitNewSpace(dialog: AlertDialog, name: String, path: String,
                               mode: String, description: String, tvError: TextView) {
        val api = this.api ?: return
        val positive = dialog.getButton(AlertDialog.BUTTON_POSITIVE)
        positive.isEnabled = false
        tvError.visibility = View.GONE
        scope.launch {
            val ok = api.createSpace(name, path, mode, description)
            val error = api.lastError
            withContext(Dispatchers.Main) {
                positive.isEnabled = true
                if (ok) {
                    dialog.dismiss()
                    Toast.makeText(this@MainActivity,
                        getString(R.string.space_created, name), Toast.LENGTH_SHORT).show()
                    currentSpace = name
                    loadSpaces()
                } else {
                    // 服务端的中文原因原样展示（重名 / 系统目录 / 路径是文件 / 保留名等）
                    val message = error ?: getString(R.string.space_create_failed)
                    tvError.text = message
                    tvError.visibility = View.VISIBLE
                    if (message.contains("口令") || message.contains("unauthorized")) {
                        showTokenGuide(dialog)
                    }
                }
            }
        }
    }

    /** 没有全局口令时：明确提示，并给出填写口令的入口。 */
    private fun showTokenGuide(formDialog: AlertDialog?) {
        AlertDialog.Builder(this)
            .setTitle(R.string.need_token_title)
            .setMessage(R.string.need_token_msg)
            .setPositiveButton(R.string.goto_fill_token) { guide, _ ->
                guide.dismiss()
                formDialog?.dismiss()
                binding.etToken.requestFocus()
                val imm = getSystemService(INPUT_METHOD_SERVICE) as InputMethodManager
                imm.showSoftInput(binding.etToken, InputMethodManager.SHOW_IMPLICIT)
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    /** 服务端目录浏览器：逐级点进、返回上一级，只显示服务端允许的目录。 */
    private fun showBrowseDialog(startPath: String, target: EditText) {
        val api = this.api ?: return
        val view = layoutInflater.inflate(R.layout.dialog_browse_dirs, null)
        val tvPath = view.findViewById<TextView>(R.id.tvBrowsePath)
        val tvHint = view.findViewById<TextView>(R.id.tvBrowseHint)
        val rootBar = view.findViewById<LinearLayout>(R.id.llBrowseRoots)
        val listView = view.findViewById<ListView>(R.id.lvBrowseDirs)
        val entries = mutableListOf<DirEntry>()
        val labels = mutableListOf<String>()
        val listAdapter = ArrayAdapter(this, R.layout.item_spinner, labels)
        listView.adapter = listAdapter
        var current = ""
        var parent = ""

        val dialog = AlertDialog.Builder(this)
            .setTitle(R.string.dlg_browse_title)
            .setView(view)
            .setPositiveButton(R.string.browse_pick, null)
            .setNeutralButton(R.string.browse_up, null)
            .setNegativeButton(R.string.cancel, null)
            .create()

        var load: (String) -> Unit = {}

        fun render(listing: DirListing) {
            current = listing.path
            parent = listing.parent
            tvPath.text = listing.path
            val state = if (listing.writable) getString(R.string.browse_writable)
            else getString(R.string.browse_readonly)
            tvHint.text = getString(
                if (listing.truncated) R.string.browse_truncated else R.string.browse_hint,
                listing.dirs.size, state)
            entries.clear()
            entries.addAll(listing.dirs)
            labels.clear()
            for (entry in entries) {
                labels.add(
                    if (entry.writable) getString(R.string.browse_dir_rw, entry.name)
                    else getString(R.string.browse_dir_ro, entry.name))
            }
            listAdapter.notifyDataSetChanged()
            dialog.getButton(AlertDialog.BUTTON_NEUTRAL).isEnabled = parent.isNotEmpty()

            rootBar.removeAllViews()
            for (root in listing.roots) {
                val chip = Button(this)
                chip.text = root
                chip.textSize = 12f
                chip.setTextColor(0xFF5B8CFF.toInt())
                chip.minWidth = 0
                chip.minimumWidth = 0
                chip.setPadding(28, 0, 28, 0)
                chip.backgroundTintList = ColorStateList.valueOf(0xFF232A44.toInt())
                chip.setOnClickListener { load(root) }
                rootBar.addView(chip)
            }
        }

        load = { path ->
            tvHint.text = getString(R.string.browse_loading)
            scope.launch {
                val listing = api.listDirs(path)
                val error = api.lastError
                withContext(Dispatchers.Main) {
                    if (listing == null) {
                        tvHint.text = error ?: getString(R.string.browse_failed)
                    } else {
                        render(listing)
                    }
                }
            }
        }

        listView.setOnItemClickListener { _, _, position, _ ->
            val entry = entries.getOrNull(position) ?: return@setOnItemClickListener
            load(joinPath(current, entry.name))
        }
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                if (current.isNotEmpty()) {
                    target.setText(current)
                    dialog.dismiss()
                }
            }
            dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener {
                if (parent.isNotEmpty()) load(parent)
            }
            load(startPath)
        }
        dialog.show()
    }

    private fun joinPath(parent: String, name: String): String {
        val sep = if (parent.contains('\\')) "\\" else "/"
        return parent.trimEnd('\\', '/') + sep + name
    }

    private fun refreshFiles() {
        val api = api ?: return
        val space = currentSpace
        scope.launch {
            val list = api.listFiles(space.ifEmpty { null })
            withContext(Dispatchers.Main) {
                adapter.update(list)
                binding.tvInfo.text = getString(R.string.files_info, space.ifEmpty { getString(R.string.space_all) }, list.size)
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
            Toast.makeText(this, getString(R.string.err_no_server), Toast.LENGTH_SHORT).show()
            return
        }
        if (opRunning) {
            Toast.makeText(this, getString(R.string.busy), Toast.LENGTH_SHORT).show()
            return
        }
        if (currentSpaceReadOnly) {
            Toast.makeText(this, getString(R.string.space_readonly_toast, currentSpace),
                Toast.LENGTH_SHORT).show()
            return
        }
        val intent = Intent(Intent.ACTION_GET_CONTENT).apply {
            type = "*/*"
            addCategory(Intent.CATEGORY_OPENABLE)
            putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
        }
        pickLauncher.launch(Intent.createChooser(intent, getString(R.string.pick_files_title)))
    }

    private data class UploadItem(val uri: Uri, val name: String, val size: Long)

    private fun uploadUris(uris: List<Uri>) {
        val api = api ?: run {
            Toast.makeText(this, getString(R.string.err_no_server), Toast.LENGTH_SHORT).show()
            return
        }
        if (opRunning) {
            Toast.makeText(this, getString(R.string.busy), Toast.LENGTH_SHORT).show()
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

            val failed = java.util.Collections.synchronizedList(mutableListOf<Uri>())
            val sent = AtomicLong(0)
            var uploaded = 0
            val space = currentSpace.takeIf { it.isNotEmpty() }

            // 每批最多 uploadConcurrency 个，避免一次选几十个文件拖死设备
            for (batch in items.chunked(uploadConcurrency)) {
                val jobs = batch.map { item ->
                    async {
                        if (item.size <= 0) {
                            failed.add(item.uri)
                            return@async
                        }
                        withContext(Dispatchers.Main) {
                            binding.tvStatus.text = getString(R.string.uploading_name, item.name)
                        }
                        var last = 0L
                        var ok = false
                        try {
                            contentResolver.openInputStream(item.uri)?.use { input ->
                                ok = api.uploadFile(item.name, input, item.size, space) { done, _ ->
                                    val delta = done - last
                                    last = done
                                    updateProgress(sent.addAndGet(delta), totalBytes,
                                        getString(R.string.upload_progress, uploaded, items.size))
                                }
                            }
                        } catch (e: Exception) {
                            ok = false
                        }
                        if (ok) uploaded++ else failed.add(item.uri)
                    }
                }
                jobs.awaitAll()
            }

            withContext(Dispatchers.Main) {
                opRunning = false
                binding.btnUpload.isEnabled = !currentSpaceReadOnly
                binding.progressBar.progress = if (failed.isEmpty()) 100 else {
                    binding.progressBar.progress
                }
                if (failed.isEmpty()) {
                    binding.tvStatus.text = getString(R.string.upload_done)
                    Toast.makeText(this@MainActivity,
                        getString(R.string.upload_done_toast, items.size), Toast.LENGTH_SHORT).show()
                    refreshFiles()
                    hideProgressLater()
                } else {
                    val msg = api.lastError ?: getString(R.string.err_network)
                    binding.tvStatus.text = getString(R.string.upload_partial, failed.size, items.size)
                    showProgress(false)
                    askRetry(getString(R.string.upload_fail_msg, msg, items.size - failed.size, items.size)) {
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
            .setTitle(getString(R.string.delete))
            .setMessage(getString(R.string.confirm_delete, f.name))
            .setPositiveButton(getString(R.string.delete)) { _, _ ->
                val api = api ?: return@setPositiveButton
                scope.launch {
                    val ok = api.deleteFile(f.name, f.space.takeIf { it.isNotEmpty() })
                    withContext(Dispatchers.Main) {
                        if (ok) {
                            Toast.makeText(this@MainActivity, getString(R.string.deleted), Toast.LENGTH_SHORT).show()
                            refreshFiles()
                        } else {
                            Toast.makeText(this@MainActivity,
                                api.lastError ?: getString(R.string.delete_fail), Toast.LENGTH_SHORT).show()
                        }
                    }
                }
            }
            .setNegativeButton(getString(R.string.cancel), null)
            .show()
    }

    // --------------------------------------------------------------------------- #
    // 下载（应用专属目录，无需任何存储权限）
    // --------------------------------------------------------------------------- #
    private fun downloadFile(f: FileItem, destOverride: File? = null) {
        val api = api ?: return
        if (opRunning) {
            Toast.makeText(this, getString(R.string.busy), Toast.LENGTH_SHORT).show()
            return
        }
        downloadDir.mkdirs()
        // 重试时复用同一个 dest，.part 文件才能被 Range 续传
        val dest = destOverride ?: uniqueLocalFile(sanitizeFileName(f.name))
        val space = f.space.ifEmpty { currentSpace }

        scope.launch {
            withContext(Dispatchers.Main) {
                opRunning = true
                resetRate()
                showProgress(true)
                binding.tvStatus.text = getString(R.string.downloading_name, f.name)
            }

            val ok = api.downloadFile(f.name, dest,
                space.takeIf { it.isNotEmpty() }) { done, total ->
                updateProgress(done, total, getString(R.string.downloading_label, f.name))
            }

            withContext(Dispatchers.Main) {
                opRunning = false
                if (ok) {
                    binding.tvStatus.text = getString(R.string.download_saved, "${downloadDir.name}/${dest.name}")
                    binding.progressBar.progress = 100
                    Toast.makeText(this@MainActivity, getString(R.string.download_done), Toast.LENGTH_SHORT).show()
                    hideProgressLater()
                } else {
                    binding.tvStatus.text = getString(R.string.download_fail)
                    showProgress(false)
                    askRetry(api.lastError ?: getString(R.string.download_fail_msg)) {
                        downloadFile(f, dest)
                    }
                }
            }
        }
    }

    // --------------------------------------------------------------------------- #
    // 进度展示
    // --------------------------------------------------------------------------- #
    private fun resetRate() {
        lastRateAt = 0L
        lastRateDone = 0L
        lastProgressAt = 0L
    }

    private fun updateProgress(done: Long, total: Long, label: String) {
        val now = System.currentTimeMillis()
        if (now - lastProgressAt < 200 && done < total) return
        val elapsed = (now - lastRateAt) / 1000.0
        val speed = if (lastRateAt > 0 && elapsed > 0.2 && done > lastRateDone) {
            (done - lastRateDone) / elapsed
        } else {
            0.0
        }
        lastProgressAt = now
        lastRateAt = now
        lastRateDone = done
        runOnUiThread {
            val pct = if (total > 0) ((done * 100) / total).toInt().coerceIn(0, 100) else 0
            binding.progressBar.progress = pct
            val sizeText = if (total > 0) {
                " · ${FileItem.formatSize(done)} / ${FileItem.formatSize(total)}"
            } else {
                " · ${FileItem.formatSize(done)}"
            }
            val speedText = if (speed > 0) {
                " · ${FileItem.formatSize(speed.toLong())}/s" +
                    if (total > 0 && done < total) {
                        getString(R.string.eta, ((total - done) / speed).toInt())
                    } else ""
            } else ""
            binding.tvStatus.text = "$label  $pct%$sizeText$speedText"
        }
    }

    private fun showProgress(show: Boolean) {
        binding.progressBar.visibility = if (show) View.VISIBLE else View.GONE
        if (!show) binding.progressBar.progress = 0
        binding.btnConnect.isEnabled = !show
        binding.btnScan.isEnabled = !show
        binding.btnUpload.isEnabled = !show && !currentSpaceReadOnly
    }

    private fun hideProgressLater() {
        mainHandler.postDelayed({
            if (!opRunning) showProgress(false)
        }, 1200)
    }

    private fun askRetry(message: String, retry: () -> Unit) {
        AlertDialog.Builder(this)
            .setTitle(R.string.dlg_error)
            .setMessage(message)
            .setPositiveButton(R.string.retry) { _, _ -> retry() }
            .setNegativeButton(getString(R.string.cancel), null)
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
        return clean.ifBlank { getString(R.string.unnamed_file) }
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
                            .setTitle(R.string.export_done_title)
                            .setMessage(getString(R.string.export_done_msg))
                            .setPositiveButton(R.string.delete_copy) { _, _ ->
                                file.delete()
                                Toast.makeText(this@MainActivity, getString(R.string.deleted), Toast.LENGTH_SHORT).show()
                            }
                            .setNegativeButton(R.string.keep, null)
                            .show()
                    } else {
                        Toast.makeText(this@MainActivity, getString(R.string.export_fail), Toast.LENGTH_SHORT).show()
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
                .setTitle(R.string.downloads_title)
                .setMessage(R.string.downloads_empty)
                .setPositiveButton(getString(R.string.close), null)
                .show()
            return
        }

        val names = files.map { "${it.name}  (${FileItem.formatSize(it.length())})" }.toTypedArray()
        AlertDialog.Builder(this)
            .setTitle(getString(R.string.downloads_count, files.size))
            .setItems(names) { _, idx -> showFileActions(files[idx]) }
            .setPositiveButton(R.string.share_all) { _, _ -> shareAllFiles(files) }
            .setNegativeButton(getString(R.string.close), null)
            .show()
    }

    /** 单个已下载文件的操作菜单 */
    private fun showFileActions(file: File) {
        val actions = arrayOf(getString(R.string.open), getString(R.string.share), getString(R.string.move_save), getString(R.string.delete))
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
            .setNegativeButton(R.string.back, null)
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
                ?: Toast.makeText(this, getString(R.string.no_app), Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            Toast.makeText(this, getString(R.string.open_fail, e.message), Toast.LENGTH_SHORT).show()
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
            Toast.makeText(this, getString(R.string.picker_fail, e.message), Toast.LENGTH_LONG).show()
        }
    }

    /** 删除 App 内的已下载文件（专属目录，无需权限） */
    private fun confirmDeleteLocal(file: File) {
        AlertDialog.Builder(this)
            .setTitle(getString(R.string.delete))
            .setMessage(getString(R.string.confirm_delete_local, file.name))
            .setPositiveButton(getString(R.string.delete)) { _, _ ->
                if (file.delete()) {
                    Toast.makeText(this, getString(R.string.deleted), Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(this, getString(R.string.delete_fail), Toast.LENGTH_SHORT).show()
                }
            }
            .setNegativeButton(getString(R.string.cancel), null)
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
            .setTitle(R.string.fm_unsupported_title)
            .setMessage(getString(R.string.fm_unsupported_msg, path))
            .setPositiveButton(R.string.copy_path) { _, _ -> copyPathToClipboard() }
            .setNegativeButton(getString(R.string.close), null)
            .show()
    }

    /** 用系统分享菜单一次性把所有下载文件发出去 */
    private fun shareAllFiles(files: List<File>) {
        if (files.isEmpty()) {
            Toast.makeText(this, getString(R.string.no_downloads_toast), Toast.LENGTH_SHORT).show()
            return
        }
        val uris = files.mapNotNull { file ->
            try {
                FileProvider.getUriForFile(this, "${packageName}.fileprovider", file)
            } catch (_: Exception) { null }
        }
        if (uris.isEmpty()) {
            Toast.makeText(this, getString(R.string.share_link_fail), Toast.LENGTH_SHORT).show()
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
        startActivity(Intent.createChooser(intent, getString(R.string.share_title)))
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
        html.append("</style></head><body><h2>" + getString(R.string.html_downloads_title, files.size) + "</h2>")
        html.append("<table><thead><tr><th>" + getString(R.string.html_col_name) + "</th><th>" + getString(R.string.html_col_size) + "</th></tr></thead><tbody>")
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
            .setTitle(R.string.html_title)
            .setPositiveButton(getString(R.string.close), null)
            .show()
    }

    private fun copyPathToClipboard() {
        val text = getString(R.string.path_copied_text, downloadDir.absolutePath)
        val cm = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("LanTransfer", text))
        Toast.makeText(this, getString(R.string.path_copied), Toast.LENGTH_SHORT).show()
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
