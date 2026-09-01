package com.maynutlab.astralpatcher.ui

import android.content.ComponentName
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.ParcelFileDescriptor
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.net.toUri
import com.maynutlab.astralpatcher.BuildConfig
import com.maynutlab.astralpatcher.IPatchService
import com.maynutlab.astralpatcher.core.CatalogIdentity
import com.maynutlab.astralpatcher.core.GAME_PACKAGE
import com.maynutlab.astralpatcher.core.PatchHttpClient
import com.maynutlab.astralpatcher.core.PatchProtocol
import com.maynutlab.astralpatcher.core.ReleaseEntry
import com.maynutlab.astralpatcher.shizuku.PatchUserService
import rikka.shizuku.Shizuku
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID
import java.util.concurrent.Executors

class MainActivity : ComponentActivity() {
    private lateinit var controller: PatchController

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        controller = PatchController(this)
        setContent {
            AstralMaterialTheme {
                PatchManagerScreen(
                    state = controller.state,
                    onRefresh = controller::refresh,
                    onShizuku = controller::handleShizuku,
                    onGame = controller::handleGame,
                    onPatch = controller::applyPatch,
                    onRestore = controller::restorePatch,
                    onLaunch = controller::launchGame,
                )
            }
        }
        controller.start()
    }

    override fun onDestroy() {
        controller.stop()
        super.onDestroy()
    }
}

data class PatchUiState(
    val gameStatus: String = "확인 중",
    val gameReady: Boolean = false,
    val shizukuStatus: String = "확인 중",
    val shizukuReady: Boolean = false,
    val resourceStatus: String = "확인 중",
    val releaseStatus: String = "확인 중",
    val patchVersion: String? = null,
    val installedPatchVersion: String? = null,
    val busy: Boolean = false,
    val progress: Float = 0f,
    val progressLabel: String? = null,
    val logs: List<String> = emptyList(),
) {
    val canPatch: Boolean
        get() = gameReady && shizukuReady && patchVersion != null &&
            patchVersion != installedPatchVersion && !busy
    val canRestore: Boolean
        get() = gameReady && shizukuReady && installedPatchVersion != null && !busy
}

private class PatchController(private val activity: MainActivity) {
    private val main = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor()
    private var service: IPatchService? = null
    private var binding = false
    private var catalog: CatalogIdentity? = null
    private var release: ReleaseEntry? = null

    var state by mutableStateOf(PatchUiState())
        private set

    private val serviceArgs = Shizuku.UserServiceArgs(
        ComponentName(activity, PatchUserService::class.java)
    )
        .daemon(false)
        .tag("astral-cache-patcher-v1")
        .version(BuildConfig.VERSION_CODE)
        .processNameSuffix("patch")

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            service = IPatchService.Stub.asInterface(binder)
            binding = false
            appendLog("Shizuku patch 서비스에 연결했습니다.")
            refresh()
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            service = null
            binding = false
            appendLog("Shizuku patch 서비스 연결이 종료되었습니다.")
            refresh()
        }
    }

    private val binderReceived = Shizuku.OnBinderReceivedListener { refresh() }
    private val binderDead = Shizuku.OnBinderDeadListener {
        service = null
        refresh()
    }
    private val permissionResult = Shizuku.OnRequestPermissionResultListener { requestCode, _ ->
        if (requestCode == SHIZUKU_PERMISSION_REQUEST) refresh()
    }

    fun start() {
        Shizuku.addBinderReceivedListenerSticky(binderReceived)
        Shizuku.addBinderDeadListener(binderDead)
        Shizuku.addRequestPermissionResultListener(permissionResult)
        refresh()
    }

    fun stop() {
        Shizuku.removeBinderReceivedListener(binderReceived)
        Shizuku.removeBinderDeadListener(binderDead)
        Shizuku.removeRequestPermissionResultListener(permissionResult)
        if (binding || service != null) {
            runCatching { Shizuku.unbindUserService(serviceArgs, serviceConnection, true) }
        }
        service = null
        executor.shutdownNow()
    }

    fun refresh() {
        if (state.busy) return
        val game = installedGame()
        val gameReady = game?.installedFromPlay == true
        val shizukuInstalled = isPackageInstalled(SHIZUKU_PACKAGE)
        val binderReady = runCatching { Shizuku.pingBinder() }.getOrDefault(false)
        val permissionGranted = binderReady &&
            runCatching { Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED }
                .getOrDefault(false)

        state = state.copy(
            gameStatus = when {
                game == null -> "원본 게임이 설치되지 않음"
                game.installedFromPlay -> "Google Play 설치본 · v${game.version}"
                else -> "Google Play 설치본이 아님 · 재설치 필요"
            },
            gameReady = gameReady,
            shizukuStatus = when {
                !shizukuInstalled -> "Shizuku가 설치되지 않음"
                !binderReady -> "설치됨 · 서비스를 시작해야 함"
                !permissionGranted -> "연결됨 · 권한 허용 필요"
                else -> "연결 및 권한 확인 완료"
            },
            shizukuReady = permissionGranted,
            resourceStatus = if (gameReady && permissionGranted) "게임 리소스를 확인 중" else "대기 중",
            releaseStatus = if (gameReady && permissionGranted) "정식 패치를 확인 중" else "대기 중",
            patchVersion = null,
            installedPatchVersion = null,
        )
        catalog = null
        release = null
        if (!gameReady || !permissionGranted) return
        if (service == null) {
            bindPatchService()
            return
        }
        inspectAndResolve()
    }

    fun handleShizuku() {
        when {
            !isPackageInstalled(SHIZUKU_PACKAGE) -> openUrl(SHIZUKU_DOWNLOAD_URL)
            !Shizuku.pingBinder() -> openPackage(SHIZUKU_PACKAGE)
            Shizuku.checkSelfPermission() != PackageManager.PERMISSION_GRANTED ->
                Shizuku.requestPermission(SHIZUKU_PERMISSION_REQUEST)
            else -> refresh()
        }
    }

    fun handleGame() {
        if (installedGame()?.installedFromPlay != true) {
            openUrl("https://play.google.com/store/apps/details?id=$GAME_PACKAGE")
        } else {
            launchGame()
        }
    }

    fun applyPatch() {
        val targetRelease = release ?: return
        val targetCatalog = catalog ?: return
        val patchService = service ?: return
        setBusy("패치 정보를 검증하는 중", 0f)
        executor.execute {
            val transactionId = UUID.randomUUID().toString()
            var transactionStarted = false
            val payloads = mutableListOf<File>()
            try {
                val manifestBytes = PatchHttpClient.getManifest(targetRelease)
                val manifest = PatchProtocol.parseManifest(manifestBytes, targetRelease)
                patchService.beginPatch(transactionId, targetCatalog.catalogHash)
                transactionStarted = true
                manifest.files.forEachIndexed { index, item ->
                    updateBusy(
                        "파일 ${index + 1}/${manifest.files.size} 다운로드 및 검증",
                        index.toFloat() / manifest.files.size,
                    )
                    val payload = PatchHttpClient.downloadPayload(
                        activity.cacheDir,
                        item,
                        index,
                    ) { percent ->
                        val overall = (index + percent / 100f) / manifest.files.size
                        updateBusy("파일 ${index + 1}/${manifest.files.size} 다운로드", overall)
                    }
                    payloads += payload
                    updateBusy(
                        "파일 ${index + 1}/${manifest.files.size} 적용",
                        (index + 0.95f) / manifest.files.size,
                    )
                    ParcelFileDescriptor.open(payload, ParcelFileDescriptor.MODE_READ_ONLY).use { descriptor ->
                        patchService.applyFile(
                            transactionId,
                            descriptor,
                            item.payloadSize,
                            item.payloadSha256,
                            item.sourceSize,
                            item.sourceSha256,
                            item.relativePath,
                        )
                    }
                }
                patchService.commitPatch(
                    transactionId,
                    manifest.gameVersion,
                    manifest.catalogHash,
                    manifest.patchVersion,
                )
                transactionStarted = false
                main.post {
                    appendLog("${manifest.patchVersion} 적용을 완료했습니다.")
                    setIdle()
                    refresh()
                }
            } catch (error: Exception) {
                if (transactionStarted) runCatching { patchService.rollbackPatch(transactionId) }
                showError("한글패치 적용에 실패했습니다.", error)
            } finally {
                payloads.forEach(File::delete)
            }
        }
    }

    fun restorePatch() {
        val patchService = service ?: return
        setBusy("원본 리소스를 복원하는 중", 0f)
        executor.execute {
            try {
                val result = patchService.restorePatch()
                main.post {
                    appendLog(result)
                    setIdle()
                    refresh()
                }
            } catch (error: Exception) {
                showError("원본 복원에 실패했습니다.", error)
            }
        }
    }

    fun launchGame() {
        val intent = activity.packageManager.getLaunchIntentForPackage(GAME_PACKAGE)
        if (intent == null) appendLog("게임 실행 Activity를 찾지 못했습니다.")
        else activity.startActivity(intent)
    }

    private fun bindPatchService() {
        if (binding) return
        binding = true
        appendLog("Shizuku patch 서비스에 연결하는 중입니다.")
        runCatching { Shizuku.bindUserService(serviceArgs, serviceConnection) }
            .onFailure {
                binding = false
                showError("Shizuku patch 서비스 연결에 실패했습니다.", it)
            }
    }

    private fun inspectAndResolve() {
        val patchService = service ?: return
        executor.execute {
            try {
                require(patchService.getServiceInfo().startsWith("AstralPatchService/1 ")) {
                    "지원하지 않는 patch 서비스입니다."
                }
                val inspected = PatchProtocol.parseInspection(patchService.inspectGame())
                val index = PatchHttpClient.getIndex()
                val resolved = PatchProtocol.resolveRelease(index, inspected)
                catalog = inspected
                release = resolved
                main.post {
                    state = state.copy(
                        resourceStatus = "게임 ${inspected.gameVersion} · catalog ${inspected.catalogHash.take(8)}…",
                        releaseStatus = when {
                            resolved == null -> "현재 리소스와 일치하는 정식 패치 없음"
                            resolved.patchVersion == inspected.installedPatchVersion ->
                                "${resolved.patchVersion} 적용 완료"
                            else -> "${resolved.patchVersion} 적용 가능"
                        },
                        patchVersion = resolved?.patchVersion,
                        installedPatchVersion = inspected.installedPatchVersion,
                    )
                }
            } catch (error: Exception) {
                showError("게임 리소스 확인에 실패했습니다.", error)
            }
        }
    }

    private fun installedGame(): GameInstall? = runCatching {
        val version = activity.packageManager.getPackageInfo(GAME_PACKAGE, 0).versionName
            ?: "알 수 없음"
        val installer = activity.packageManager.getInstallSourceInfo(GAME_PACKAGE).installingPackageName
        GameInstall(version, installer == PLAY_STORE_PACKAGE)
    }.getOrNull()

    private fun isPackageInstalled(packageName: String): Boolean = runCatching {
        activity.packageManager.getApplicationInfo(packageName, 0).enabled
    }.getOrDefault(false)

    private fun openPackage(packageName: String) {
        activity.packageManager.getLaunchIntentForPackage(packageName)?.let(activity::startActivity)
            ?: appendLog("앱을 열 수 없습니다: $packageName")
    }

    private fun openUrl(value: String) {
        activity.startActivity(Intent(Intent.ACTION_VIEW, value.toUri()))
    }

    private fun setBusy(label: String, progress: Float) {
        state = state.copy(busy = true, progressLabel = label, progress = progress)
        appendLog(label)
    }

    private fun updateBusy(label: String, progress: Float) {
        main.post {
            state = state.copy(progressLabel = label, progress = progress.coerceIn(0f, 1f))
        }
    }

    private fun setIdle() {
        state = state.copy(busy = false, progressLabel = null, progress = 0f)
    }

    private fun showError(prefix: String, error: Throwable) {
        main.post {
            appendLog("$prefix ${error.message ?: error.javaClass.simpleName}")
            setIdle()
        }
    }

    private fun appendLog(message: String) {
        if (message.isBlank()) return
        val action = {
            val timestamp = SimpleDateFormat("HH:mm:ss", Locale.KOREA).format(Date())
            state = state.copy(logs = (state.logs + "[$timestamp] ${message.trim()}").takeLast(60))
        }
        if (Looper.myLooper() == Looper.getMainLooper()) action() else main.post(action)
    }

    companion object {
        private const val SHIZUKU_PACKAGE = "moe.shizuku.privileged.api"
        private const val PLAY_STORE_PACKAGE = "com.android.vending"
        private const val SHIZUKU_DOWNLOAD_URL = "https://shizuku.rikka.app/download/"
        private const val SHIZUKU_PERMISSION_REQUEST = 1001
    }
}

private data class GameInstall(val version: String, val installedFromPlay: Boolean)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PatchManagerScreen(
    state: PatchUiState,
    onRefresh: () -> Unit,
    onShizuku: () -> Unit,
    onGame: () -> Unit,
    onPatch: () -> Unit,
    onRestore: () -> Unit,
    onLaunch: () -> Unit,
) {
    Scaffold(
        modifier = Modifier.fillMaxSize(),
        topBar = {
            TopAppBar(
                modifier = Modifier.statusBarsPadding(),
                title = {
                    Column {
                        Text("Astral Patch Manager", fontWeight = FontWeight.SemiBold)
                        Text("원본 게임을 유지하는 한글패치", style = MaterialTheme.typography.labelMedium)
                    }
                },
                actions = { TextButton(onClick = onRefresh, enabled = !state.busy) { Text("새로고침") } },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
            )
        },
    ) { innerPadding ->
        Column(
            modifier = Modifier
                .padding(innerPadding)
                .padding(horizontal = 20.dp)
                .navigationBarsPadding()
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Spacer(Modifier.height(4.dp))
            StatusCard("원본 게임", state.gameStatus, state.gameReady, onGame)
            StatusCard("Shizuku", state.shizukuStatus, state.shizukuReady, onShizuku)
            StatusCard("게임 리소스", state.resourceStatus, state.gameReady && state.shizukuReady)
            StatusCard(
                "한글패치",
                state.releaseStatus,
                state.patchVersion != null,
            )

            if (state.busy) {
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer)) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text(state.progressLabel.orEmpty(), fontWeight = FontWeight.Medium)
                        LinearProgressIndicator(
                            progress = { state.progress },
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
            }

            Button(
                onClick = onPatch,
                enabled = state.canPatch,
                modifier = Modifier.fillMaxWidth().height(52.dp),
                shape = RoundedCornerShape(18.dp),
            ) {
                Text("한글패치 적용")
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedButton(onClick = onRestore, enabled = state.canRestore, modifier = Modifier.weight(1f)) {
                    Text("원본 복원")
                }
                OutlinedButton(onClick = onLaunch, enabled = state.gameReady && !state.busy, modifier = Modifier.weight(1f)) {
                    Text("게임 실행")
                }
            }

            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("실행 기록", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    HorizontalDivider()
                    SelectionContainer {
                        Text(
                            if (state.logs.isEmpty()) "아직 기록이 없습니다." else state.logs.joinToString("\n\n"),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            Spacer(Modifier.height(16.dp))
        }
    }
}

@Composable
private fun StatusCard(
    title: String,
    detail: String,
    ready: Boolean,
    onClick: (() -> Unit)? = null,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        onClick = { onClick?.invoke() },
        enabled = onClick != null,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Surface(
                color = if (ready) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline,
                shape = RoundedCornerShape(50),
                modifier = Modifier.height(12.dp),
            ) { Box(Modifier.height(12.dp).padding(horizontal = 6.dp)) }
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Medium)
                Text(detail, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (onClick != null) Text("설정", color = MaterialTheme.colorScheme.primary)
        }
    }
}

@Composable
private fun AstralMaterialTheme(content: @Composable () -> Unit) {
    val context = LocalContext.current
    val dark = isSystemInDarkTheme()
    val colors = if (android.os.Build.VERSION.SDK_INT >= 31) {
        if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
    } else if (dark) {
        darkColorScheme(primary = Color(0xFF91CEF4), secondary = Color(0xFFB8C8DA))
    } else {
        lightColorScheme(primary = Color(0xFF006493), secondary = Color(0xFF4E616F))
    }
    MaterialTheme(colorScheme = colors, content = content)
}
