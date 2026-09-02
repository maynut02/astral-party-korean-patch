package com.maynutlab.astralpatcher.ui

import android.content.ComponentName
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.provider.Settings
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.LocalMinimumInteractiveComponentSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import com.maynutlab.astralpatcher.BuildConfig
import com.maynutlab.astralpatcher.IPatchService
import com.maynutlab.astralpatcher.R
import com.maynutlab.astralpatcher.core.CatalogIdentity
import com.maynutlab.astralpatcher.core.GAME_PACKAGE
import com.maynutlab.astralpatcher.core.MobilePatcherRelease
import com.maynutlab.astralpatcher.core.OriginalGameRelease
import com.maynutlab.astralpatcher.core.PatchHttpClient
import com.maynutlab.astralpatcher.core.PatchManifest
import com.maynutlab.astralpatcher.core.PatchProtocol
import com.maynutlab.astralpatcher.core.SHIZUKU_PACKAGE
import com.maynutlab.astralpatcher.shizuku.PatchUserService
import rikka.shizuku.Shizuku
import java.io.File
import java.security.MessageDigest
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
                    onUpdate = controller::updatePatcher,
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

    override fun onResume() {
        super.onResume()
        if (::controller.isInitialized) controller.resume()
    }
}

private data class PatchUiState(
    val gameStatus: String = "확인 중",
    val gameReady: Boolean = false,
    val gameActionLabel: String = "설정",
    val shizukuStatus: String = "확인 중",
    val shizukuReady: Boolean = false,
    val shizukuActionLabel: String = "설정",
    val resourceStatus: String = "확인 중",
    val resourceIndicator: StatusIndicator = StatusIndicator.IN_PROGRESS,
    val resourceNeedsDownload: Boolean = false,
    val releaseStatus: String = "확인 중",
    val releaseIndicator: StatusIndicator = StatusIndicator.IN_PROGRESS,
    val patchVersion: String? = null,
    val installedPatchVersion: String? = null,
    val availablePatcherVersion: String? = null,
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

private enum class StatusIndicator {
    PENDING,
    IN_PROGRESS,
    COMPLETED,
    ERROR,
}

private class PatchController(private val activity: MainActivity) {
    private val main = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor()
    private val updateExecutor = Executors.newSingleThreadExecutor()
    private var service: IPatchService? = null
    private var binding = false
    private var catalog: CatalogIdentity? = null
    private var manifest: PatchManifest? = null
    @Volatile
    private var latestPatcher: MobilePatcherRelease? = null
    private var pendingSystemInstall: PendingInstall? = null
    @Volatile
    private var inspecting = false
    @Volatile
    private var updateChecking = false

    var state by mutableStateOf(PatchUiState())
        private set

    private val serviceArgs = Shizuku.UserServiceArgs(
        ComponentName(activity, PatchUserService::class.java)
    )
        .daemon(false)
        .tag("astral-cache-patcher-v5")
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
        updateExecutor.shutdownNow()
    }

    fun resume() {
        val pending = pendingSystemInstall
        if (pending != null && activity.packageManager.canRequestPackageInstalls()) {
            pendingSystemInstall = null
            openSystemInstaller(pending.apk)
        } else if (!state.busy) {
            refresh()
        }
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
                game == null -> "Astral Party가 설치되지 않음"
                game.installedFromPlay -> "Google Play 설치본 · v${game.version}"
                else -> "Google Play 설치본이 아님 · 재설치 필요"
            },
            gameReady = gameReady,
            gameActionLabel = if (gameReady) "실행" else "게임 설치",
            shizukuStatus = when {
                !shizukuInstalled -> "Shizuku가 설치되지 않음"
                !binderReady -> "설치됨 · 페어링 및 서비스 시작 필요"
                !permissionGranted -> "연결됨 · 권한 허용 필요"
                else -> "연결 및 권한 확인 완료"
            },
            shizukuReady = permissionGranted,
            shizukuActionLabel = when {
                !shizukuInstalled -> "설치"
                !binderReady -> "열기"
                !permissionGranted -> "권한 허용"
                else -> "열기"
            },
            resourceStatus = if (gameReady && permissionGranted) "게임 리소스를 확인 중" else "대기 중",
            resourceIndicator = if (gameReady && permissionGranted) {
                StatusIndicator.IN_PROGRESS
            } else {
                StatusIndicator.PENDING
            },
            resourceNeedsDownload = false,
            releaseStatus = if (gameReady && permissionGranted) "정식 패치를 확인 중" else "대기 중",
            releaseIndicator = if (gameReady && permissionGranted) {
                StatusIndicator.IN_PROGRESS
            } else {
                StatusIndicator.PENDING
            },
            patchVersion = null,
            installedPatchVersion = null,
        )
        catalog = null
        manifest = null
        checkPatcherUpdate()
        if (!permissionGranted) {
            return
        }
        if (service == null) {
            bindPatchService()
            return
        }
        if (!gameReady) return
        inspectAndResolve()
    }

    fun handleShizuku() {
        if (state.busy) return
        when {
            !isPackageInstalled(SHIZUKU_PACKAGE) -> {
                val pending = pendingSystemInstall
                if (pending?.target == InstallTarget.SHIZUKU && pending.apk.isFile) {
                    requestSystemInstall(pending.apk, InstallTarget.SHIZUKU)
                } else {
                    installShizuku()
                }
            }
            !Shizuku.pingBinder() -> openPackage(SHIZUKU_PACKAGE)
            Shizuku.checkSelfPermission() != PackageManager.PERMISSION_GRANTED -> {
                if (Shizuku.shouldShowRequestPermissionRationale()) {
                    appendLog("Shizuku에서 한글패치 앱 권한을 허용해 주세요.")
                    openPackage(SHIZUKU_PACKAGE)
                } else {
                    Shizuku.requestPermission(SHIZUKU_PERMISSION_REQUEST)
                }
            }
            else -> openPackage(SHIZUKU_PACKAGE)
        }
    }

    fun handleGame() {
        if (installedGame()?.installedFromPlay == true) {
            launchGame()
            return
        }
        if (!state.shizukuReady) {
            appendLog("원본 게임을 설치하려면 먼저 Shizuku를 실행하고 권한을 허용해 주세요.")
            handleShizuku()
            return
        }
        val patchService = service
        if (patchService == null) {
            appendLog("Shizuku 설치 서비스에 연결한 뒤 다시 시도해 주세요.")
            bindPatchService()
            return
        }
        installOriginalGame(patchService)
    }

    private fun installOriginalGame(patchService: IPatchService) {
        if (state.busy) return
        setBusy("Google Play 원본 게임 정보를 확인하는 중", 0f)
        executor.execute {
            val installId = UUID.randomUUID().toString()
            var installStarted = false
            var downloaded = emptyList<File>()
            try {
                require(patchService.getServiceInfo().startsWith("AstralPatchService/5 ")) {
                    "지원하지 않는 게임 설치 서비스입니다."
                }
                val release = PatchHttpClient.getLatestOriginalGameRelease()
                appendLog(
                    "원본 게임 ${release.versionName}(${release.versionCode}) · " +
                        "${release.files.size}개 split · profile=${release.deviceProfile}"
                )
                downloaded = PatchHttpClient.downloadOriginalGameApks(
                    activity.cacheDir,
                    release,
                ) { name, percent ->
                    updateBusy("원본 게임 APK 다운로드 · $name", percent / 100f * 0.8f)
                }
                verifyOriginalGameApks(downloaded, release)
                appendLog("원본 게임 APK 해시, packageName, versionCode 및 Play 인증서를 확인했습니다.")

                patchService.beginGameInstall(installId, downloaded.size)
                installStarted = true
                downloaded.forEachIndexed { index, apk ->
                    val metadata = release.files[index]
                    updateBusy(
                        "원본 게임 APK staging ${index + 1}/${downloaded.size}",
                        0.8f + (index.toFloat() / downloaded.size) * 0.15f,
                    )
                    ParcelFileDescriptor.open(apk, ParcelFileDescriptor.MODE_READ_ONLY).use { descriptor ->
                        patchService.stageGameApk(
                            installId,
                            metadata.name,
                            descriptor,
                            metadata.size,
                            metadata.sha256,
                        )
                    }
                }
                updateBusy("원본 split APK를 설치하는 중", 0.96f)
                val result = patchService.commitGameInstall(installId)
                installStarted = false
                verifyInstalledOriginalGame(release)
                main.post {
                    appendLog("원본 게임 설치 완료 · $result")
                    setIdle()
                    refresh()
                }
            } catch (error: Exception) {
                if (installStarted) runCatching { patchService.cancelGameInstall(installId) }
                showError("원본 게임 설치에 실패했습니다.", error)
            } finally {
                downloaded.forEach(File::delete)
            }
        }
    }

    private fun installShizuku() {
        setBusy("최신 Shizuku 안정 버전을 확인하는 중", 0f)
        updateExecutor.execute {
            var apk: File? = null
            try {
                val latest = PatchHttpClient.getLatestShizukuRelease()
                updateBusy("Shizuku ${latest.version} APK 다운로드", 0f)
                val downloadedApk = PatchHttpClient.downloadShizukuApk(activity.cacheDir, latest) { percent ->
                    updateBusy("Shizuku ${latest.version} APK 다운로드", percent / 100f)
                }
                apk = downloadedApk
                verifyShizukuApk(downloadedApk)
                main.post {
                    appendLog("Shizuku ${latest.version} APK 다운로드 및 검증을 완료했습니다.")
                    setIdle()
                    requestSystemInstall(downloadedApk, InstallTarget.SHIZUKU)
                }
            } catch (error: Exception) {
                apk?.delete()
                showError("Shizuku 다운로드에 실패했습니다.", error)
            }
        }
    }

    fun updatePatcher() {
        if (state.busy) return
        val pending = pendingSystemInstall
        if (pending?.target == InstallTarget.PATCHER && pending.apk.isFile) {
            requestSystemInstall(pending.apk, InstallTarget.PATCHER)
            return
        }
        val target = latestPatcher?.takeIf { it.isNewerThan(BuildConfig.VERSION_CODE) } ?: return
        setBusy("Android 패처 ${target.version} 다운로드", 0f)
        updateExecutor.execute {
            var apk: File? = null
            try {
                val downloadedApk = PatchHttpClient.downloadMobilePatcherApk(
                    activity.cacheDir,
                    target,
                ) { percent ->
                    updateBusy("Android 패처 ${target.version} 다운로드", percent / 100f)
                }
                apk = downloadedApk
                verifyMobilePatcherApk(downloadedApk, target)
                main.post {
                    appendLog("Android 패처 ${target.version} APK 다운로드 및 검증을 완료했습니다.")
                    setIdle()
                    requestSystemInstall(downloadedApk, InstallTarget.PATCHER)
                }
            } catch (error: Exception) {
                apk?.delete()
                showError("Android 패처 업데이트 다운로드에 실패했습니다.", error)
            }
        }
    }

    fun applyPatch() {
        val targetManifest = manifest ?: return
        val targetCatalog = catalog ?: return
        val patchService = service ?: return
        state = state.copy(releaseIndicator = StatusIndicator.IN_PROGRESS)
        setBusy("패치 정보를 검증하는 중", 0f)
        executor.execute {
            val transactionId = UUID.randomUUID().toString()
            var transactionStarted = false
            var diagnosticsCursor = 0
            val payloads = mutableListOf<File>()
            val originals = mutableListOf<File>()
            try {
                val serviceInfo = patchService.getServiceInfo()
                appendLog(
                    "[CLIENT] patch 시작 · app=${BuildConfig.VERSION_NAME}(${BuildConfig.VERSION_CODE}) " +
                        "transaction=$transactionId"
                )
                appendLog("[CLIENT] service=$serviceInfo")
                appendLog(
                    "[CLIENT] manifest · patch=${targetManifest.patchVersion} " +
                        "game=${targetManifest.gameVersion} revision=${targetManifest.revision} " +
                        "catalog=${targetManifest.catalogHash} files=${targetManifest.files.size}"
                )
                val readiness = PatchProtocol.parseTargetInspection(
                    patchService.inspectPatchTargets(
                        PatchProtocol.createTargetInspectionRequest(targetManifest)
                    )
                )
                appendLog(
                    "[CLIENT] 리소스 사전검사 · total=${readiness.total} ready=${readiness.ready} " +
                        "missing=${readiness.missing} incompatible=${readiness.incompatible}"
                )
                require(readiness.isReady) {
                    "게임 리소스가 변경되었거나 필요한 파일이 없습니다. 새로고침 후 다시 시도해 주세요."
                }
                appendLog("[CLIENT] beginPatch 호출 · transaction=$transactionId")
                transactionStarted = true
                patchService.beginPatch(transactionId, targetCatalog.catalogHash)
                appendLog("[CLIENT] beginPatch 반환")
                diagnosticsCursor = appendServiceDiagnostics(
                    patchService,
                    transactionId,
                    diagnosticsCursor,
                    includeSnapshot = true,
                )
                targetManifest.files.forEachIndexed { index, item ->
                    val fileLabel = "${index + 1}/${targetManifest.files.size}"
                    val original = PatchHttpClient.downloadOriginal(
                        activity.cacheDir,
                        item,
                        index,
                    ) { percent ->
                        val overall = (index + percent / 200f) / targetManifest.files.size
                        updateBusy("원본 파일 $fileLabel 다운로드", overall)
                    }
                    originals += original
                    ParcelFileDescriptor.open(original, ParcelFileDescriptor.MODE_READ_ONLY).use { descriptor ->
                        patchService.stageOriginal(
                            transactionId,
                            descriptor,
                            item.sourceSize,
                            item.sourceSha256,
                            item.relativePath,
                        )
                    }
                    appendLog("[CLIENT] 파일 $fileLabel 릴리즈 원본 검증 및 보관 완료")
                    appendLog(
                        "[CLIENT] 파일 $fileLabel 다운로드 시작 · path=${item.relativePath} " +
                            "downloadBytes=${item.downloadSize} payloadBytes=${item.payloadSize} " +
                            "payloadSha256=${item.payloadSha256} sourceBytes=${item.sourceSize} " +
                            "sourceSha256=${item.sourceSha256}"
                    )
                    updateBusy(
                        "파일 $fileLabel 다운로드 및 검증",
                        index.toFloat() / targetManifest.files.size,
                    )
                    val payload = PatchHttpClient.downloadPayload(
                        activity.cacheDir,
                        item,
                        index,
                    ) { percent ->
                        val overall = (index + percent / 100f) / targetManifest.files.size
                        updateBusy("파일 $fileLabel 다운로드", overall)
                    }
                    payloads += payload
                    appendLog("[CLIENT] 파일 $fileLabel 다운로드 검증 완료 · localBytes=${payload.length()}")
                    updateBusy(
                        "파일 $fileLabel 적용",
                        (index + 0.95f) / targetManifest.files.size,
                    )
                    appendLog("[CLIENT] 파일 $fileLabel applyFile 호출 · path=${item.relativePath}")
                    val applyResultJson = ParcelFileDescriptor.open(payload, ParcelFileDescriptor.MODE_READ_ONLY).use { descriptor ->
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
                    val applyResult = PatchProtocol.parseApplyFileResult(applyResultJson)
                    appendLog(
                        if (applyResult.ok) {
                            "[CLIENT] 파일 $fileLabel applyFile 성공"
                        } else {
                            "[CLIENT] 파일 $fileLabel applyFile 실패 · ${applyResult.error}"
                        }
                    )
                    diagnosticsCursor = appendServiceDiagnostics(
                        patchService,
                        transactionId,
                        diagnosticsCursor,
                        includeSnapshot = true,
                    )
                    require(applyResult.ok) {
                        applyResult.error.ifBlank { "patch 파일 적용에 실패했습니다." }
                    }
                }
                appendLog("[CLIENT] commitPatch 호출")
                patchService.commitPatch(
                    transactionId,
                    targetManifest.gameVersion,
                    targetManifest.catalogHash,
                    targetManifest.patchVersion,
                )
                transactionStarted = false
                appendLog("[CLIENT] commitPatch 반환")
                diagnosticsCursor = appendServiceDiagnostics(
                    patchService,
                    transactionId,
                    diagnosticsCursor,
                    includeSnapshot = true,
                )
                main.post {
                    appendLog("${targetManifest.patchVersion} 적용을 완료했습니다.")
                    setIdle()
                    refresh()
                }
            } catch (error: Exception) {
                main.post { state = state.copy(releaseIndicator = StatusIndicator.ERROR) }
                appendLog("[CLIENT] patch 예외 · ${describeError(error)}")
                diagnosticsCursor = appendServiceDiagnostics(
                    patchService,
                    transactionId,
                    diagnosticsCursor,
                    includeSnapshot = true,
                )
                if (transactionStarted) {
                    appendLog("[CLIENT] rollbackPatch 호출")
                    runCatching { patchService.rollbackPatch(transactionId) }
                        .onSuccess { appendLog("[CLIENT] rollbackPatch 반환") }
                        .onFailure { rollbackError ->
                            appendLog("[CLIENT] rollbackPatch 예외 · ${describeError(rollbackError)}")
                        }
                    appendServiceDiagnostics(
                        patchService,
                        transactionId,
                        diagnosticsCursor,
                        includeSnapshot = true,
                    )
                }
                showError("한글패치 적용에 실패했습니다.", error)
            } finally {
                payloads.forEach(File::delete)
                originals.forEach(File::delete)
            }
        }
    }

    fun restorePatch() {
        val patchService = service ?: return
        state = state.copy(releaseIndicator = StatusIndicator.IN_PROGRESS)
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
                main.post { state = state.copy(releaseIndicator = StatusIndicator.ERROR) }
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
        if (inspecting) return
        inspecting = true
        executor.execute {
            var inspected: CatalogIdentity? = null
            var checkingTargets = false
            try {
                require(patchService.getServiceInfo().startsWith("AstralPatchService/5 ")) {
                    "지원하지 않는 patch 서비스입니다."
                }
                val catalogIdentity = PatchProtocol.parseInspection(patchService.inspectGame())
                inspected = catalogIdentity
                val index = PatchHttpClient.getIndex()
                val resolved = PatchProtocol.resolveRelease(index, catalogIdentity)
                catalog = catalogIdentity
                if (resolved == null) {
                    manifest = null
                    main.post {
                        state = state.copy(
                            resourceStatus = "검사할 정식 패치가 없습니다",
                            resourceIndicator = StatusIndicator.PENDING,
                            resourceNeedsDownload = false,
                            releaseStatus = "현재 게임과 일치하는 정식 패치 없음",
                            releaseIndicator = StatusIndicator.PENDING,
                            patchVersion = null,
                            installedPatchVersion = catalogIdentity.installedPatchVersion,
                        )
                    }
                    return@execute
                }

                val resolvedManifest = PatchProtocol.parseManifest(
                    PatchHttpClient.getManifest(resolved),
                    resolved,
                )
                checkingTargets = true
                val targetInspection = PatchProtocol.parseTargetInspection(
                    patchService.inspectPatchTargets(
                        PatchProtocol.createTargetInspectionRequest(resolvedManifest)
                    )
                )
                val ready = targetInspection.isReady
                manifest = resolvedManifest.takeIf { ready }
                main.post {
                    state = state.copy(
                        resourceStatus = when {
                            ready -> "패치 대상 리소스 ${targetInspection.total}개 확인 완료"
                            targetInspection.missing > 0 && targetInspection.incompatible > 0 ->
                                "리소스 ${targetInspection.missing}개 없음 · ${targetInspection.incompatible}개 불일치"
                            targetInspection.missing > 0 ->
                                "필요한 게임 리소스 ${targetInspection.missing}개가 없습니다"
                            else -> "게임 리소스 ${targetInspection.incompatible}개가 현재 패치와 다릅니다"
                        },
                        resourceIndicator = when {
                            ready -> StatusIndicator.COMPLETED
                            targetInspection.incompatible > 0 -> StatusIndicator.ERROR
                            else -> StatusIndicator.PENDING
                        },
                        resourceNeedsDownload = !ready,
                        releaseStatus = when {
                            !ready -> "${resolved.patchVersion} · 게임 리소스 필요"
                            resolved.patchVersion == catalogIdentity.installedPatchVersion ->
                                "${resolved.patchVersion} 적용 완료"
                            else -> "${resolved.patchVersion} 적용 가능"
                        },
                        releaseIndicator = when {
                            !ready && targetInspection.incompatible > 0 -> StatusIndicator.ERROR
                            !ready -> StatusIndicator.PENDING
                            resolved.patchVersion == catalogIdentity.installedPatchVersion ->
                                StatusIndicator.COMPLETED
                            else -> StatusIndicator.PENDING
                        },
                        patchVersion = resolved.patchVersion.takeIf { ready },
                        installedPatchVersion = catalogIdentity.installedPatchVersion,
                    )
                }
            } catch (error: Exception) {
                val needsDownload = isResourceNotReady(error)
                val currentCatalog = inspected
                main.post {
                    state = state.copy(
                        resourceStatus = when {
                            needsDownload -> "게임을 실행해 리소스를 다운로드해 주세요"
                            currentCatalog != null && !checkingTargets -> "패치 대상 리소스를 확인할 수 없음"
                            else -> "게임 리소스를 확인할 수 없음"
                        },
                        resourceIndicator = if (needsDownload) {
                            StatusIndicator.PENDING
                        } else {
                            StatusIndicator.ERROR
                        },
                        resourceNeedsDownload = needsDownload,
                        releaseStatus = if (currentCatalog != null && !checkingTargets) {
                            "정식 패치 정보를 확인할 수 없음"
                        } else {
                            "게임 리소스 확인 필요"
                        },
                        releaseIndicator = if (needsDownload) {
                            StatusIndicator.PENDING
                        } else {
                            StatusIndicator.ERROR
                        },
                        patchVersion = null,
                        installedPatchVersion = currentCatalog?.installedPatchVersion,
                    )
                    appendLog("패치 상태 확인에 실패했습니다. ${error.message ?: error.javaClass.simpleName}")
                    setIdle()
                }
            } finally {
                inspecting = false
            }
        }
    }

    private fun checkPatcherUpdate() {
        if (updateChecking) return
        updateChecking = true
        updateExecutor.execute {
            try {
                val latest = PatchHttpClient.getLatestMobilePatcherRelease()
                val available = latest.takeIf { it.isNewerThan(BuildConfig.VERSION_CODE) }
                latestPatcher = available
                main.post {
                    state = state.copy(availablePatcherVersion = available?.version)
                }
            } catch (error: Exception) {
                latestPatcher = null
                main.post {
                    state = state.copy(availablePatcherVersion = null)
                    appendLog(
                        "Android 패처 업데이트 확인에 실패했습니다. " +
                            (error.message ?: error.javaClass.simpleName)
                    )
                }
            } finally {
                updateChecking = false
            }
        }
    }

    private fun isResourceNotReady(error: Throwable): Boolean =
        generateSequence(error) { it.cause }
            .mapNotNull(Throwable::message)
            .any { message ->
                RESOURCE_NOT_READY_MESSAGES.any(message::contains)
            }

    private fun installedGame(): GameInstall? = runCatching {
        val version = activity.packageManager.getPackageInfo(GAME_PACKAGE, 0).versionName
            ?: "알 수 없음"
        val installer = activity.packageManager.getInstallSourceInfo(GAME_PACKAGE).installingPackageName
        GameInstall(version, installer == PLAY_STORE_PACKAGE)
    }.getOrNull()

    @Suppress("DEPRECATION")
    private fun verifyOriginalGameApks(apks: List<File>, release: OriginalGameRelease) {
        require(apks.size == release.files.size) { "다운로드한 원본 게임 APK 파일 수가 다릅니다." }
        apks.forEachIndexed { index, apk ->
            val expected = release.files[index]
            require(apk.name == expected.name && apk.isFile && apk.length() == expected.size) {
                "다운로드한 원본 게임 APK 파일 정보가 다릅니다: ${expected.name}"
            }
        }
        val base = apks.single { it.name == "base.apk" }
        val info = activity.packageManager.getPackageArchiveInfo(
            base.absolutePath,
            PackageManager.GET_SIGNING_CERTIFICATES,
        ) ?: error("원본 게임 base APK를 해석할 수 없습니다.")
        require(info.packageName == GAME_PACKAGE) { "원본 게임 APK packageName이 다릅니다." }
        require(info.longVersionCode == release.versionCode) {
            "원본 게임 APK versionCode가 다릅니다."
        }
        require(certificateSha256(info) == release.certificateSha256) {
            "원본 게임 APK Play 인증서가 다릅니다."
        }
    }

    @Suppress("DEPRECATION")
    private fun verifyInstalledOriginalGame(release: OriginalGameRelease) {
        val info = activity.packageManager.getPackageInfo(
            GAME_PACKAGE,
            PackageManager.GET_SIGNING_CERTIFICATES,
        )
        require(info.longVersionCode == release.versionCode) {
            "설치된 게임 versionCode가 원본 release와 다릅니다."
        }
        require(certificateSha256(info) == release.certificateSha256) {
            "설치된 게임 인증서가 Google Play 원본과 다릅니다."
        }
        val source = activity.packageManager.getInstallSourceInfo(GAME_PACKAGE)
        require(source.installingPackageName == PLAY_STORE_PACKAGE) {
            "설치자 기록이 Google Play로 설정되지 않았습니다: ${source.installingPackageName}"
        }
    }

    private fun certificateSha256(info: PackageInfo): String {
        val signers = info.signingInfo?.apkContentsSigners.orEmpty()
        require(signers.size == 1) { "APK의 현재 signing certificate 수가 올바르지 않습니다." }
        return MessageDigest.getInstance("SHA-256")
            .digest(signers.single().toByteArray())
            .joinToString(separator = "") { "%02x".format(it.toInt() and 0xff) }
    }

    private fun isPackageInstalled(packageName: String): Boolean = runCatching {
        activity.packageManager.getApplicationInfo(packageName, 0).enabled
    }.getOrDefault(false)

    @Suppress("DEPRECATION")
    private fun verifyShizukuApk(apk: File) {
        val info = activity.packageManager.getPackageArchiveInfo(apk.absolutePath, 0)
        require(info?.packageName == SHIZUKU_PACKAGE) {
            "다운로드한 APK packageName이 Shizuku와 다릅니다."
        }
    }

    @Suppress("DEPRECATION")
    private fun verifyMobilePatcherApk(apk: File, release: MobilePatcherRelease) {
        val archive = activity.packageManager.getPackageArchiveInfo(
            apk.absolutePath,
            PackageManager.GET_SIGNING_CERTIFICATES,
        )
        require(archive?.packageName == activity.packageName) {
            "다운로드한 APK packageName이 현재 Android 패처와 다릅니다."
        }
        require(archive.versionName == release.version) {
            "다운로드한 Android 패처 버전이 index와 다릅니다."
        }
        require(archive.longVersionCode == release.versionCode.toLong()) {
            "다운로드한 Android 패처 versionCode가 index와 다릅니다."
        }
        require(archive.longVersionCode > BuildConfig.VERSION_CODE.toLong()) {
            "현재 버전보다 새로운 Android 패처가 아닙니다."
        }
        val installed = activity.packageManager.getPackageInfo(
            activity.packageName,
            PackageManager.GET_SIGNING_CERTIFICATES,
        )
        require(hasSameCurrentSigners(installed, archive)) {
            "다운로드한 Android 패처 APK 서명이 현재 앱과 다릅니다."
        }
    }

    private fun hasSameCurrentSigners(first: PackageInfo, second: PackageInfo): Boolean {
        val firstSigners = first.signingInfo?.apkContentsSigners ?: return false
        val secondSigners = second.signingInfo?.apkContentsSigners ?: return false
        return firstSigners.size == secondSigners.size &&
            firstSigners.all { signer -> secondSigners.any(signer::equals) }
    }

    private fun requestSystemInstall(apk: File, target: InstallTarget) {
        if (!activity.packageManager.canRequestPackageInstalls()) {
            pendingSystemInstall = PendingInstall(apk, target)
            appendLog("이 앱의 '알 수 없는 앱 설치' 권한을 허용해 주세요.")
            activity.startActivity(
                Intent(
                    Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:${activity.packageName}"),
                )
            )
            return
        }
        openSystemInstaller(apk)
    }

    private fun openSystemInstaller(apk: File) {
        require(apk.isFile) { "설치할 Shizuku APK를 찾지 못했습니다." }
        val uri = FileProvider.getUriForFile(activity, "${activity.packageName}.files", apk)
        activity.startActivity(
            Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, "application/vnd.android.package-archive")
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }

    private fun openPackage(packageName: String) {
        activity.packageManager.getLaunchIntentForPackage(packageName)?.let(activity::startActivity)
            ?: appendLog("앱을 열 수 없습니다: $packageName")
    }

    private fun openUrl(value: String) {
        activity.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(value)))
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

    private fun appendServiceDiagnostics(
        patchService: IPatchService,
        transactionId: String,
        cursor: Int,
        includeSnapshot: Boolean,
    ): Int = runCatching {
        val diagnostics = PatchProtocol.parsePatchDiagnostics(
            patchService.getPatchDiagnostics(transactionId),
            transactionId,
        )
        val start = cursor.takeIf { it in 0..diagnostics.events.size } ?: 0
        if (start == 0) {
            appendLog(
                "[SERVICE] ${diagnostics.serviceInfo} · diagnosticsFile=" +
                    "${diagnostics.diagnosticsFileExists}/${diagnostics.diagnosticsFileBytes}bytes"
            )
        }
        diagnostics.events.drop(start).forEach { event ->
            val eventTime = SimpleDateFormat("HH:mm:ss.SSS", Locale.KOREA)
                .format(Date(event.timestampMs))
            val detail = event.detail.takeIf(String::isNotBlank)?.let { " · $it" }.orEmpty()
            appendLog(
                "[SERVICE $eventTime pid=${event.pid} uid=${event.uid} thread=${event.thread}] " +
                    "${event.stage}$detail"
            )
        }
        if (diagnostics.readError.isNotBlank()) {
            appendLog("[SERVICE] 진단 파일 읽기 오류 · ${diagnostics.readError}")
        }
        if (includeSnapshot) {
            val transaction = diagnostics.transaction
            appendLog(
                "[SERVICE] transaction snapshot · exists=${transaction.exists} " +
                    "path=${transaction.path} catalog=${transaction.catalogExists}/${transaction.catalogBytes}bytes " +
                    "journal=${transaction.journalExists}/${transaction.journalBytes}bytes/" +
                    "${transaction.journalEntries}entries previous=${transaction.previousFiles}files " +
                    "staging=${transaction.stagingFiles}files"
            )
            if (transaction.journalReadError.isNotBlank()) {
                appendLog("[SERVICE] journal 읽기 오류 · ${transaction.journalReadError}")
            }
        }
        diagnostics.events.size
    }.getOrElse { error ->
        appendLog("[CLIENT] 서비스 진단 회수 실패 · ${describeError(error)}")
        cursor
    }

    private fun describeError(error: Throwable): String =
        generateSequence(error) { it.cause }
            .take(8)
            .joinToString(" <- ") { cause ->
                "${cause.javaClass.name}: ${cause.message.orEmpty()}"
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
            state = state.copy(logs = (state.logs + "[$timestamp] ${message.trim()}").takeLast(300))
        }
        if (Looper.myLooper() == Looper.getMainLooper()) action() else main.post(action)
    }

    companion object {
        private const val PLAY_STORE_PACKAGE = "com.android.vending"
        private const val SHIZUKU_PERMISSION_REQUEST = 1001
        private val RESOURCE_NOT_READY_MESSAGES = listOf(
            "외부 files 디렉터리를 찾지 못했습니다",
            "게임을 먼저 실행해 리소스를 다운로드",
            "Addressables bundle cache를 찾지 못했습니다",
            "게임 catalog hash를 찾지 못했습니다",
        )
    }
}

private data class GameInstall(val version: String, val installedFromPlay: Boolean)
private data class PendingInstall(val apk: File, val target: InstallTarget)

private enum class InstallTarget {
    SHIZUKU,
    PATCHER,
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PatchManagerScreen(
    state: PatchUiState,
    onRefresh: () -> Unit,
    onUpdate: () -> Unit,
    onShizuku: () -> Unit,
    onGame: () -> Unit,
    onPatch: () -> Unit,
    onRestore: () -> Unit,
    onLaunch: () -> Unit,
) {
    val context = LocalContext.current
    Scaffold(
        modifier = Modifier.fillMaxSize(),
        topBar = {
            TopAppBar(
                modifier = Modifier.statusBarsPadding(),
                title = {
                    Text("아스트랄 파티 한글패치", fontWeight = FontWeight.SemiBold)
                },
                actions = {
                    IconButton(
                        onClick = onRefresh,
                        enabled = !state.busy,
                        modifier = Modifier.padding(end = 8.dp),
                    ) {
                        Icon(
                            painter = painterResource(R.drawable.ic_refresh_24),
                            contentDescription = "새로고침",
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = Color.Transparent,
                    scrolledContainerColor = Color.Transparent,
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
            StatusCard(
                "Shizuku",
                state.shizukuStatus,
                when {
                    state.shizukuStatus == "확인 중" -> StatusIndicator.IN_PROGRESS
                    state.shizukuReady -> StatusIndicator.COMPLETED
                    else -> StatusIndicator.PENDING
                },
                onShizuku,
                state.shizukuActionLabel,
            )
            StatusCard(
                "Astral Party",
                state.gameStatus,
                when {
                    state.gameStatus == "확인 중" -> StatusIndicator.IN_PROGRESS
                    state.gameReady -> StatusIndicator.COMPLETED
                    else -> StatusIndicator.PENDING
                },
                onGame,
                state.gameActionLabel,
            )
            StatusCard(
                "게임 리소스",
                state.resourceStatus,
                state.resourceIndicator,
                onClick = if (state.resourceNeedsDownload) onLaunch else null,
                actionLabel = "게임 실행",
            )
            StatusCard(
                "한글패치",
                state.releaseStatus,
                state.releaseIndicator,
            )

            state.availablePatcherVersion?.let { version ->
                Card(
                    modifier = Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.tertiaryContainer,
                    ),
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text(
                                "Android 패처 $version 업데이트",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold,
                            )
                            Text(
                                "새 버전을 다운로드하고 설치할 수 있습니다.",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onTertiaryContainer,
                            )
                        }
                        FilledTonalButton(
                            onClick = onUpdate,
                            enabled = !state.busy,
                            modifier = Modifier.fillMaxWidth().height(48.dp),
                            shape = RoundedCornerShape(16.dp),
                        ) {
                            Text("업데이트 설치")
                        }
                    }
                }
            }

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
                FilledTonalButton(
                    onClick = onRestore,
                    enabled = state.canRestore,
                    modifier = Modifier.weight(1f).height(52.dp),
                    shape = RoundedCornerShape(18.dp),
                ) {
                    Text("원본 복원")
                }
                Button(
                    onClick = onLaunch,
                    enabled = state.gameReady && !state.busy,
                    modifier = Modifier.weight(1f).height(52.dp),
                    shape = RoundedCornerShape(18.dp),
                ) {
                    Text("게임 실행")
                }
            }

            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            "실행 기록",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.weight(1f),
                        )
                        CompositionLocalProvider(LocalMinimumInteractiveComponentSize provides 36.dp) {
                            IconButton(
                                onClick = {
                                    val clipboard = activityClipboardManager(context)
                                    clipboard.setPrimaryClip(
                                        android.content.ClipData.newPlainText(
                                            "실행 기록",
                                            state.logs.joinToString("\n\n"),
                                        )
                                    )
                                },
                                enabled = state.logs.isNotEmpty(),
                                modifier = Modifier.size(36.dp),
                            ) {
                                Icon(
                                    painter = painterResource(R.drawable.ic_content_copy_24),
                                    contentDescription = "실행 기록 복사",
                                    modifier = Modifier.size(18.dp),
                                )
                            }
                        }
                    }
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
    indicator: StatusIndicator,
    onClick: (() -> Unit)? = null,
    actionLabel: String = "설정",
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        onClick = { onClick?.invoke() },
        enabled = onClick != null,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerLow,
            disabledContainerColor = MaterialTheme.colorScheme.surfaceContainerLow,
            disabledContentColor = MaterialTheme.colorScheme.onSurface,
        ),
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Surface(
                color = when (indicator) {
                    StatusIndicator.PENDING -> Color(0xFFFFB300)
                    StatusIndicator.IN_PROGRESS -> MaterialTheme.colorScheme.primary
                    StatusIndicator.COMPLETED -> Color(0xFF43A047)
                    StatusIndicator.ERROR -> MaterialTheme.colorScheme.error
                },
                shape = RoundedCornerShape(50),
                modifier = Modifier.height(12.dp),
            ) { Box(Modifier.height(12.dp).padding(horizontal = 6.dp)) }
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Medium)
                Text(detail, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            if (onClick != null) Text(actionLabel, color = MaterialTheme.colorScheme.primary)
        }
    }
}

private fun activityClipboardManager(context: android.content.Context): android.content.ClipboardManager =
    context.getSystemService(android.content.ClipboardManager::class.java)

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
