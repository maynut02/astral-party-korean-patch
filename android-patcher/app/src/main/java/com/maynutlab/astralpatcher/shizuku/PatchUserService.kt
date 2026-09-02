package com.maynutlab.astralpatcher.shizuku

import android.os.ParcelFileDescriptor
import android.system.Os
import android.system.OsConstants
import androidx.annotation.Keep
import com.maynutlab.astralpatcher.IPatchService
import com.maynutlab.astralpatcher.core.GAME_PACKAGE
import com.maynutlab.astralpatcher.core.PatchProtocol
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.SyncFailedException
import java.security.MessageDigest
import java.util.Locale
import java.util.concurrent.TimeUnit

@Keep
class PatchUserService : IPatchService.Stub() {
    private val filesRoot = File("/storage/emulated/0/Android/data/$GAME_PACKAGE/files")
    private val addressablesRoot = File(filesRoot, "com.unity.addressables")
    private val bundleRoot = File(addressablesRoot, "AssetBundles")
    private val stateRoot = File(filesRoot, ".astralpatch-manager")
    private val transactionsRoot = File(stateRoot, "transactions")
    private val backupsRoot = File(stateRoot, "backups")
    private val stateFile = File(stateRoot, "state.json")
    private val diagnosticsFile = File(stateRoot, "patch-diagnostics.jsonl")
    private val gameInstallRoot = File("/data/local/tmp/astral-original-installer")
    private var activeGameInstallId: String? = null
    private var expectedGameApkCount = 0
    private var gameInstallStatus = "idle"

    override fun getServiceInfo(): String =
        "AstralPatchService/5 uid=${android.os.Process.myUid()} pid=${android.os.Process.myPid()} " +
            "installer=$gameInstallStatus"

    @Synchronized
    override fun beginGameInstall(installId: String, fileCount: Int) {
        val safeId = requireTransactionId(installId)
        require(activeGameInstallId == null) { "다른 게임 설치가 이미 진행 중입니다." }
        require(fileCount in 1..MAX_GAME_APK_FILES) { "설치할 게임 APK 파일 수가 올바르지 않습니다." }
        cleanupGameInstallRoot()
        require(gameInstallRoot.isDirectory || gameInstallRoot.mkdirs()) {
            "게임 APK staging root를 만들 수 없습니다."
        }
        val staging = gameInstallDirectory(safeId)
        require(staging.mkdir()) { "게임 APK staging 디렉터리를 만들 수 없습니다." }
        activeGameInstallId = safeId
        expectedGameApkCount = fileCount
        gameInstallStatus = "staging 0/$fileCount"
    }

    @Synchronized
    override fun stageGameApk(
        installId: String,
        name: String,
        apk: ParcelFileDescriptor,
        size: Long,
        sha256: String,
    ) {
        val safeId = requireActiveGameInstall(installId)
        require(name.matches(SAFE_APK_NAME)) { "게임 APK 파일 이름이 안전하지 않습니다." }
        val expectedSize = requirePositive(size, "게임 APK 크기")
        val expectedSha = requireSha256(sha256)
        val staging = gameInstallDirectory(safeId)
        val target = File(staging, name)
        require(target.parentFile == staging && !target.exists()) {
            "게임 APK staging 경로가 올바르지 않거나 중복되었습니다."
        }
        receiveGameApk(apk, target, expectedSize, expectedSha)
        Os.chmod(target.path, 0b110100100)
        val stagedCount = stagedGameApks(staging).size
        require(stagedCount <= expectedGameApkCount) { "예상보다 많은 게임 APK가 전달되었습니다." }
        gameInstallStatus = "staging $stagedCount/$expectedGameApkCount"
    }

    @Synchronized
    override fun commitGameInstall(installId: String): String {
        val safeId = requireActiveGameInstall(installId)
        val staging = gameInstallDirectory(safeId)
        val apks = stagedGameApks(staging)
        require(apks.size == expectedGameApkCount) {
            "게임 APK 파일 수가 다릅니다: ${apks.size}/$expectedGameApkCount"
        }
        require(apks.any { it.name == "base.apk" }) { "base APK가 staging에 없습니다." }
        gameInstallStatus = "installing ${apks.size} files"
        var packageSessionId: Int? = null
        return try {
            val totalSize = apks.sumOf(File::length)
            val createResult = runInstallCommand(
                listOf(
                    "/system/bin/pm",
                    "install-create",
                    "-S",
                    totalSize.toString(),
                    "-r",
                    "-i",
                    PLAY_STORE_PACKAGE,
                )
            )
            val sessionId = INSTALL_SESSION_ID.find(createResult)
                ?.groupValues
                ?.get(1)
                ?.toIntOrNull()
                ?: error("package install session ID를 확인할 수 없습니다: $createResult")
            packageSessionId = sessionId
            apks.forEachIndexed { index, apk ->
                gameInstallStatus = "writing ${index + 1}/${apks.size} session=$sessionId"
                runInstallCommand(
                    listOf(
                        "/system/bin/pm",
                        "install-write",
                        "-S",
                        apk.length().toString(),
                        sessionId.toString(),
                        apk.name,
                        apk.absolutePath,
                    )
                )
            }
            gameInstallStatus = "committing session=$sessionId"
            val result = runInstallCommand(
                listOf(
                    "/system/bin/pm",
                    "install-commit",
                    sessionId.toString(),
                )
            )
            packageSessionId = null
            gameInstallStatus = "success ${apks.size} files"
            result
        } catch (error: Exception) {
            packageSessionId?.let { sessionId ->
                runCatching {
                    runInstallCommand(
                        listOf(
                            "/system/bin/pm",
                            "install-abandon",
                            sessionId.toString(),
                        )
                    )
                }
            }
            gameInstallStatus = "failed ${error.message.orEmpty().take(MAX_STATUS_OUTPUT_CHARS)}"
            throw error
        } finally {
            finishGameInstall(staging)
        }
    }

    @Synchronized
    override fun cancelGameInstall(installId: String) {
        val safeId = requireTransactionId(installId)
        if (activeGameInstallId != safeId) return
        finishGameInstall(gameInstallDirectory(safeId))
        gameInstallStatus = "cancelled"
    }

    @Synchronized
    override fun inspectGame(): String {
        require(filesRoot.isDirectory) { "Astral Party의 외부 files 디렉터리를 찾지 못했습니다." }
        require(addressablesRoot.isDirectory) { "게임을 먼저 실행해 리소스를 다운로드해 주세요." }
        require(bundleRoot.isDirectory) { "Addressables bundle cache를 찾지 못했습니다." }
        recoverInterruptedTransactions()
        val catalog = discoverCatalog()
        val state = readState()
        return JSONObject()
            .put("schemaVersion", 1)
            .put("gameVersion", catalog.first)
            .put("catalogHash", catalog.second)
            .put(
                "patchVersion",
                state?.takeIf { it.optString("catalogHash") == catalog.second }
                    ?.optString("patchVersion")
                    .orEmpty(),
            )
            .toString()
    }

    @Synchronized
    override fun inspectPatchTargets(requirementsJson: String): String {
        recoverInterruptedTransactions()
        require(requirementsJson.toByteArray(Charsets.UTF_8).size <= MAX_INSPECTION_REQUEST_BYTES) {
            "리소스 검사 요청이 허용 크기를 초과합니다."
        }
        val request = JSONObject(requirementsJson)
        require(request.getInt("schemaVersion") == 1) { "지원하지 않는 리소스 검사 요청입니다." }
        val catalogHash = requireCatalogHash(request.getString("catalogHash"))
        require(discoverCatalog().second == catalogHash) { "게임 catalog가 patch manifest와 다릅니다." }
        require(request.getString("patchVersion").isNotBlank()) { "patch version이 비어 있습니다." }
        val files = request.getJSONArray("files")
        require(files.length() in 1..MAX_PATCH_FILES) { "검사할 patch 파일 수가 올바르지 않습니다." }

        var ready = 0
        var missing = 0
        var incompatible = 0
        var patchedWithoutBackup = 0
        for (index in 0 until files.length()) {
            val item = files.getJSONObject(index)
            val relativePath = PatchProtocol.safeRelativePath(item.getString("path"))
            val sourceSize = requirePositive(item.getLong("sourceSize"), "원본 파일 크기")
            val sourceSha = requireSha256(item.getString("sourceSha256"))
            val payloadSize = requirePositive(item.getLong("payloadSize"), "patch 파일 크기")
            val payloadSha = requireSha256(item.getString("payloadSha256"))
            val target = findExistingBundle(relativePath)
            if (target == null) {
                missing += 1
                continue
            }

            val actualPath = target.relativeTo(bundleRoot).invariantSeparatorsPath
            val backup = File(File(backupsRoot, catalogHash), actualPath)
            val backupExists = backup.isFile
            val backupMatches = backupExists &&
                backup.length() == sourceSize &&
                sha256(backup) == sourceSha

            if (backupExists && !backupMatches) {
                incompatible += 1
            } else if (backupMatches) {
                ready += 1
            } else {
                val targetLength = target.length()
                val targetHash = if (targetLength == sourceSize || targetLength == payloadSize) {
                    sha256(target)
                } else {
                    null
                }
                val sourceMatches = targetLength == sourceSize && targetHash == sourceSha
                val payloadMatches = targetLength == payloadSize && targetHash == payloadSha
                if (sourceMatches) {
                    ready += 1
                } else {
                    if (payloadMatches) patchedWithoutBackup += 1
                    incompatible += 1
                }
            }
        }

        return JSONObject()
            .put("schemaVersion", 1)
            .put("total", files.length())
            .put("ready", ready)
            .put("missing", missing)
            .put("incompatible", incompatible)
            .put("patchedWithoutBackup", patchedWithoutBackup)
            .toString()
    }

    @Synchronized
    override fun beginPatch(transactionId: String, catalogHash: String) {
        val safeId = requireTransactionId(transactionId)
        val expectedHash = requireCatalogHash(catalogHash)
        resetDiagnostics(safeId, "catalog=$expectedHash")
        diagnosticOperation(safeId, "begin_patch") {
            forceStopGame()
            appendDiagnostic(safeId, "game_stopped", "package=$GAME_PACKAGE")
            recoverInterruptedTransactions()
            appendDiagnostic(safeId, "recovery_complete", "이전 transaction 정리 완료")
            require(discoverCatalog().second == expectedHash) { "게임 catalog가 패치 확인 후 변경되었습니다." }
            val transaction = transactionRoot(safeId)
            require(!transaction.exists()) { "같은 patch transaction이 이미 존재합니다." }
            require(transaction.mkdirs()) { "patch transaction 디렉터리를 만들 수 없습니다." }
            writeTextAtomic(File(transaction, "catalog.txt"), expectedHash)
            appendDiagnostic(safeId, "transaction_created", transactionDetail(transaction))
        }
    }

    @Synchronized
    override fun stageOriginal(
        transactionId: String,
        original: ParcelFileDescriptor,
        sourceSize: Long,
        sourceSha256: String,
        relativePath: String,
    ) {
        val safeId = requireTransactionId(transactionId)
        diagnosticOperation(safeId, "stage_original", "path=$relativePath sourceSize=$sourceSize") {
            val expectedSize = requirePositive(sourceSize, "원본 파일 크기")
            val expectedSha = requireSha256(sourceSha256)
            val safeRelative = PatchProtocol.safeRelativePath(relativePath)
            val transaction = activeTransaction(safeId)
            val catalogHash = requireCatalogHash(File(transaction, "catalog.txt").readText().trim())
            val target = resolveExistingBundle(safeRelative)
            val actualPath = target.relativeTo(bundleRoot).invariantSeparatorsPath

            val permanentBackup = File(File(backupsRoot, catalogHash), actualPath)
            if (permanentBackup.isFile) {
                verifyFile(permanentBackup, expectedSize, expectedSha, "보관된 원본")
                original.close()
                appendDiagnostic(safeId, "release_original_reused", "path=$actualPath")
                return@diagnosticOperation
            }

            verifyFile(target, expectedSize, expectedSha, "게임 원본")
            val staged = File(transaction, "originals/$actualPath")
            receiveVerified(original, staged, expectedSize, expectedSha, safeId)
            copyVerified(staged, permanentBackup, safeId, "release_original_backup")
            staged.delete()
            appendDiagnostic(safeId, "release_original_stored", "path=$actualPath bytes=$expectedSize")
        }
    }

    @Synchronized
    override fun applyFile(
        transactionId: String,
        payload: ParcelFileDescriptor,
        size: Long,
        sha256: String,
        sourceSize: Long,
        sourceSha256: String,
        relativePath: String,
    ): String {
        val safeId = requireTransactionId(transactionId)
        val requestedPath = relativePath.take(MAX_DIAGNOSTIC_DETAIL_CHARS)
        return try {
            diagnosticOperation(
                safeId,
                "apply_file",
                "path=$requestedPath payloadSize=$size sourceSize=$sourceSize",
            ) {
                require(size > 0) { "payload 크기가 올바르지 않습니다." }
                require(sourceSize > 0) { "원본 파일 크기가 올바르지 않습니다." }
                val expectedSha = requireSha256(sha256)
                val expectedSourceSha = requireSha256(sourceSha256)
                val safePath = PatchProtocol.safeRelativePath(relativePath)
                val transaction = activeTransaction(safeId)
                val catalogHash = File(transaction, "catalog.txt").readText().trim()
                val target = resolveExistingBundle(safePath)
                val actualPath = target.relativeTo(bundleRoot).invariantSeparatorsPath
                val journal = File(transaction, "journal.txt")
                val applied = readJournal(journal)
                appendDiagnostic(
                    safeId,
                    "target_resolved",
                    "path=$actualPath targetBytes=${target.length()} ${journalDetail(journal)}",
                )
                require(actualPath !in applied) { "같은 파일을 두 번 적용할 수 없습니다: $actualPath" }

                val permanentBackup = File(File(backupsRoot, catalogHash), actualPath)
                require(permanentBackup.isFile) { "릴리즈 원본 backup이 준비되지 않았습니다." }
                verifyFile(permanentBackup, sourceSize, expectedSourceSha, "보관된 릴리즈 원본")
                appendDiagnostic(safeId, "backup_verified", "path=$actualPath 릴리즈 원본 backup 사용")
                val previous = File(transaction, "previous/$actualPath")
                copyVerified(target, previous, safeId, "transaction_previous")
                appendDiagnostic(safeId, "previous_saved", "path=$actualPath bytes=${previous.length()}")

                val staged = File(transaction, "staging/$actualPath")
                receiveVerified(payload, staged, size, expectedSha, safeId)
                appendDiagnostic(
                    safeId,
                    "payload_verified",
                    "path=$actualPath bytes=${staged.length()} sha256=$expectedSha",
                )
                appendJournal(journal, actualPath, safeId)
                appendDiagnostic(safeId, "journal_written", "path=$actualPath ${journalDetail(journal)}")
                replaceFile(staged, target, safeId, "patch_target")
                appendDiagnostic(safeId, "target_replaced", "path=$actualPath bytes=${target.length()}")
                verifyFile(target, size, expectedSha, "적용된 patch")
                appendDiagnostic(safeId, "target_verified", "path=$actualPath sha256=$expectedSha")
            }
            PatchProtocol.createApplyFileResult()
        } catch (error: Exception) {
            PatchProtocol.createApplyFileResult(error)
        }
    }

    @Synchronized
    override fun commitPatch(
        transactionId: String,
        gameVersion: String,
        catalogHash: String,
        patchVersion: String,
    ) {
        val safeId = requireTransactionId(transactionId)
        diagnosticOperation(
            safeId,
            "commit_patch",
            "gameVersion=$gameVersion patchVersion=$patchVersion catalog=$catalogHash",
        ) {
            require(gameVersion.isNotBlank() && patchVersion.isNotBlank()) { "patch 상태 정보가 비어 있습니다." }
            val expectedHash = requireCatalogHash(catalogHash)
            val transaction = activeTransaction(safeId)
            val journal = File(transaction, "journal.txt")
            appendDiagnostic(safeId, "commit_snapshot", transactionDetail(transaction))
            require(File(transaction, "catalog.txt").readText().trim() == expectedHash) {
                "transaction catalog가 다릅니다."
            }
            require(readJournal(journal).isNotEmpty()) { "적용된 patch 파일이 없습니다." }
            val state = JSONObject()
                .put("schemaVersion", 1)
                .put("gameVersion", gameVersion)
                .put("catalogHash", expectedHash)
                .put("patchVersion", patchVersion)
            writeTextAtomic(stateFile, state.toString(2))
            appendDiagnostic(safeId, "state_written", "path=${stateFile.path} bytes=${stateFile.length()}")
            deleteRecursively(transaction)
            appendDiagnostic(safeId, "transaction_deleted", "exists=${transaction.exists()}")
        }
    }

    @Synchronized
    override fun rollbackPatch(transactionId: String) {
        val safeId = requireTransactionId(transactionId)
        diagnosticOperation(safeId, "rollback_patch") {
            val transaction = transactionRoot(safeId)
            appendDiagnostic(safeId, "rollback_snapshot", transactionDetail(transaction))
            if (transaction.isDirectory) rollbackTransaction(transaction)
            appendDiagnostic(safeId, "rollback_complete", "transactionExists=${transaction.exists()}")
        }
    }

    @Synchronized
    override fun getPatchDiagnostics(transactionId: String): String {
        val safeId = requireTransactionId(transactionId)
        val transaction = transactionRoot(safeId)
        val events = JSONArray()
        var readError = ""
        if (diagnosticsFile.isFile) {
            runCatching {
                diagnosticsFile.useLines { lines ->
                    lines.take(MAX_DIAGNOSTIC_EVENTS).forEach { line ->
                        if (line.isNotBlank()) {
                            val event = JSONObject(line)
                            if (event.optString("transactionId") == safeId) events.put(event)
                        }
                    }
                }
            }.onFailure { error ->
                readError = error.message ?: error.javaClass.simpleName
            }
        }
        return JSONObject()
            .put("schemaVersion", 1)
            .put("serviceInfo", getServiceInfo())
            .put("transactionId", safeId)
            .put("events", events)
            .put("transaction", transactionSnapshot(transaction))
            .put("diagnosticsFileExists", diagnosticsFile.isFile)
            .put("diagnosticsFileBytes", diagnosticsFile.takeIf(File::isFile)?.length() ?: 0L)
            .put("readError", readError)
            .toString()
    }

    @Synchronized
    override fun restorePatch(): String {
        forceStopGame()
        recoverInterruptedTransactions()
        val state = readState() ?: return "복원할 한글패치가 없습니다."
        val catalogHash = requireCatalogHash(state.getString("catalogHash"))
        require(discoverCatalog().second == catalogHash) {
            "게임 catalog가 패치 적용 후 변경되어 자동 복원을 중단했습니다."
        }
        val backupRoot = File(backupsRoot, catalogHash)
        require(backupRoot.isDirectory) { "원본 backup을 찾지 못했습니다." }
        val backups = backupRoot.walkTopDown().filter { it.isFile }.toList()
        require(backups.isNotEmpty()) { "원본 backup이 비어 있습니다." }
        backups.forEach { backup ->
            val relative = backup.relativeTo(backupRoot).invariantSeparatorsPath
            replaceFile(backup, safePath(bundleRoot, relative))
        }
        if (!stateFile.delete()) error("patch 상태 파일을 정리할 수 없습니다.")
        deleteRecursively(backupRoot)
        return "원본 파일 ${backups.size}개를 복원했습니다."
    }

    private fun forceStopGame() {
        val process = ProcessBuilder("/system/bin/am", "force-stop", GAME_PACKAGE)
            .redirectErrorStream(true)
            .start()
        val output = process.inputStream.bufferedReader().use { it.readText().take(1024) }
        val exit = process.waitFor()
        require(exit == 0) { "게임 종료에 실패했습니다: $output" }
    }

    override fun destroy() {
        activeGameInstallId?.let { finishGameInstall(gameInstallDirectory(it)) }
        System.exit(0)
    }

    private fun receiveGameApk(
        descriptor: ParcelFileDescriptor,
        target: File,
        expectedSize: Long,
        expectedSha: String,
    ) {
        val digest = MessageDigest.getInstance("SHA-256")
        var total = 0L
        ParcelFileDescriptor.AutoCloseInputStream(descriptor).use { input ->
            FileOutputStream(target).use { output ->
                val buffer = ByteArray(BUFFER_SIZE)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    total += read
                    require(total <= expectedSize) { "게임 APK stream이 예상 크기를 초과했습니다." }
                    output.write(buffer, 0, read)
                    digest.update(buffer, 0, read)
                }
                output.flush()
                output.fd.sync()
            }
        }
        require(total == expectedSize) { "게임 APK stream 크기가 다릅니다." }
        require(digest.digest().toHex() == expectedSha) { "게임 APK stream SHA-256이 다릅니다." }
        verifyFile(target, expectedSize, expectedSha, "staging된 게임 APK")
    }

    private fun runInstallCommand(command: List<String>): String {
        val process = ProcessBuilder(command).redirectErrorStream(true).start()
        val captured = ByteArrayOutputStream()
        val reader = Thread(
            {
                process.inputStream.use { input ->
                    val buffer = ByteArray(4 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        val remaining = MAX_INSTALL_OUTPUT_BYTES - captured.size()
                        if (remaining > 0) captured.write(buffer, 0, minOf(read, remaining))
                    }
                }
            },
            "pm-install-output",
        ).apply { start() }
        try {
            if (!process.waitFor(INSTALL_TIMEOUT_SECONDS, TimeUnit.SECONDS)) {
                process.destroyForcibly()
                error("게임 APK 설치 시간이 초과되었습니다.")
            }
            reader.join(OUTPUT_READER_JOIN_TIMEOUT_MS)
            val output = captured.toString(Charsets.UTF_8.name()).trim()
            val operation = command.getOrNull(1) ?: "install"
            require(process.exitValue() == 0) {
                "pm $operation 실패(exit=${process.exitValue()}): " +
                    output.take(MAX_STATUS_OUTPUT_CHARS)
            }
            return output.ifBlank { "Success" }.take(MAX_STATUS_OUTPUT_CHARS)
        } finally {
            if (process.isAlive) process.destroyForcibly()
            if (reader.isAlive) reader.join(OUTPUT_READER_JOIN_TIMEOUT_MS)
        }
    }

    private fun requireActiveGameInstall(installId: String): String {
        val safeId = requireTransactionId(installId)
        require(activeGameInstallId == safeId) { "활성 게임 설치 session이 아닙니다." }
        return safeId
    }

    private fun gameInstallDirectory(installId: String): File {
        val target = File(gameInstallRoot, requireTransactionId(installId))
        require(target.parentFile == gameInstallRoot) { "게임 설치 경로가 안전하지 않습니다." }
        return target
    }

    private fun stagedGameApks(staging: File): List<File> =
        staging.listFiles()
            ?.filter { it.isFile && it.name.matches(SAFE_APK_NAME) }
            ?.sortedWith(compareBy<File> { it.name != "base.apk" }.thenBy(File::getName))
            .orEmpty()

    private fun finishGameInstall(staging: File) {
        deleteRecursively(staging)
        activeGameInstallId = null
        expectedGameApkCount = 0
    }

    private fun cleanupGameInstallRoot() {
        gameInstallRoot.listFiles()?.forEach(::deleteRecursively)
    }

    private fun discoverCatalog(): Pair<String, String> {
        val candidates = addressablesRoot.listFiles()
            ?.filter { it.isFile && it.name.startsWith("catalog_") && it.name.endsWith(".hash") }
            .orEmpty()
        val selected = candidates.maxWithOrNull { left, right ->
            compareVersions(catalogVersion(left), catalogVersion(right))
        } ?: error("게임 catalog hash를 찾지 못했습니다.")
        val version = catalogVersion(selected)
        val hash = requireCatalogHash(selected.readText().trim())
        return version to hash
    }

    private fun recoverInterruptedTransactions() {
        transactionsRoot.listFiles()?.filter { it.isDirectory }?.forEach(::rollbackTransaction)
    }

    private fun rollbackTransaction(transaction: File) {
        val journal = File(transaction, "journal.txt")
        readJournal(journal).asReversed().forEach { relative ->
            val previous = File(transaction, "previous/$relative")
            if (previous.isFile) replaceFile(previous, safePath(bundleRoot, relative))
        }
        deleteRecursively(transaction)
    }

    private fun activeTransaction(transactionId: String): File {
        val transaction = transactionRoot(requireTransactionId(transactionId))
        require(transaction.isDirectory && File(transaction, "catalog.txt").isFile) {
            "활성 patch transaction을 찾지 못했습니다."
        }
        return transaction
    }

    private fun transactionRoot(transactionId: String): File = safePath(transactionsRoot, transactionId)

    private fun resolveExistingBundle(relativePath: String): File {
        return findExistingBundle(relativePath)
            ?: error("게임이 아직 필요한 리소스를 다운로드하지 않았습니다: $relativePath")
    }

    private fun findExistingBundle(relativePath: String): File? {
        val exact = safePath(bundleRoot, relativePath)
        if (exact.isFile) return exact
        val alternate = when {
            relativePath.endsWith("/__data") -> "${relativePath}__"
            relativePath.endsWith("/__data__") -> relativePath.dropLast(2)
            else -> null
        }
        if (alternate != null) {
            val candidate = safePath(bundleRoot, alternate)
            if (candidate.isFile) return candidate
        }
        return null
    }

    private fun receiveVerified(
        descriptor: ParcelFileDescriptor,
        target: File,
        expectedSize: Long,
        expectedSha: String,
        transactionId: String,
    ) {
        ensureParent(target)
        val digest = MessageDigest.getInstance("SHA-256")
        var total = 0L
        ParcelFileDescriptor.AutoCloseInputStream(descriptor).use { raw ->
            BufferedInputStream(raw).use { input ->
                FileOutputStream(target).use { fileOutput ->
                    val output = BufferedOutputStream(fileOutput)
                    val buffer = ByteArray(BUFFER_SIZE)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        total += read
                        require(total <= expectedSize) { "payload stream이 예상 크기를 초과했습니다." }
                        output.write(buffer, 0, read)
                        digest.update(buffer, 0, read)
                    }
                    output.flush()
                    syncBestEffort(fileOutput, transactionId, "payload_staging")
                }
            }
        }
        require(total == expectedSize) { "payload stream 크기가 다릅니다." }
        require(digest.digest().toHex() == expectedSha) { "payload stream SHA-256이 다릅니다." }
        verifyFile(target, expectedSize, expectedSha, "수신된 payload")
    }

    private fun copyVerified(
        source: File,
        target: File,
        transactionId: String? = null,
        operation: String = "copy",
    ) {
        val size = source.length()
        val hash = sha256(source)
        copyFile(source, target, transactionId, operation)
        verifyFile(target, size, hash, "backup")
    }

    private fun copyFile(
        source: File,
        target: File,
        transactionId: String? = null,
        operation: String = "copy",
    ) {
        ensureParent(target)
        BufferedInputStream(FileInputStream(source)).use { input ->
            FileOutputStream(target).use { fileOutput ->
                val output = BufferedOutputStream(fileOutput)
                input.copyTo(output, BUFFER_SIZE)
                output.flush()
                syncBestEffort(fileOutput, transactionId, operation)
            }
        }
    }

    private fun replaceFile(
        source: File,
        target: File,
        transactionId: String? = null,
        operation: String = "replace",
    ) {
        require(source.isFile) { "교체할 원본 파일을 찾을 수 없습니다: $source" }
        require(target.isFile) { "교체할 게임 리소스를 찾을 수 없습니다: $target" }
        val expectedSize = source.length()
        val expectedSha = sha256(source)

        // Android/data 아래의 게임 소유 디렉터리는 shell UID가 기존 파일을 읽고
        // 덮어쓸 수 있어도 새 sibling 파일 생성은 거부할 수 있다. 대상 파일을
        // 삭제/rename하지 않고 기존 inode를 그대로 유지한 채 내용을 교체한다.
        if (transactionId != null) {
            appendDiagnostic(
                transactionId,
                "replace_in_place",
                "operation=$operation target=${target.path} bytes=$expectedSize",
            )
        }
        overwriteExistingFile(source, target, transactionId, operation)
        verifyFile(target, expectedSize, expectedSha, "교체된 파일")
    }

    private fun overwriteExistingFile(
        source: File,
        target: File,
        transactionId: String? = null,
        operation: String,
    ) {
        require(target.isFile) { "덮어쓸 게임 리소스를 찾을 수 없습니다: $target" }
        BufferedInputStream(FileInputStream(source)).use { input ->
            // O_CREAT를 사용하지 않아 Android/data 디렉터리의 새 파일 생성 제한을 피한다.
            val descriptor = Os.open(
                target.path,
                OsConstants.O_WRONLY or OsConstants.O_TRUNC,
                0,
            )
            FileOutputStream(descriptor).use { fileOutput ->
                val output = BufferedOutputStream(fileOutput)
                input.copyTo(output, BUFFER_SIZE)
                output.flush()
                syncBestEffort(fileOutput, transactionId, operation)
            }
        }
    }

    private fun verifyFile(file: File, expectedSize: Long, expectedSha: String, label: String) {
        require(file.isFile && file.length() == expectedSize && sha256(file) == expectedSha) {
            "$label 검증에 실패했습니다: $file"
        }
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        BufferedInputStream(FileInputStream(file)).use { input ->
            val buffer = ByteArray(BUFFER_SIZE)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().toHex()
    }

    private fun appendJournal(journal: File, relativePath: String, transactionId: String) {
        ensureParent(journal)
        FileOutputStream(journal, true).use { output ->
            output.write((relativePath + "\n").toByteArray(Charsets.UTF_8))
            output.flush()
            syncBestEffort(output, transactionId, "journal")
        }
        require(readJournal(journal).lastOrNull() == relativePath) { "patch journal 기록을 확인할 수 없습니다." }
    }

    private fun syncBestEffort(
        output: FileOutputStream,
        transactionId: String? = null,
        operation: String,
    ) {
        try {
            output.fd.sync()
        } catch (_: SyncFailedException) {
            if (transactionId != null) {
                appendDiagnostic(transactionId, "fsync_unsupported", "operation=$operation")
            }
        }
    }

    private fun readJournal(journal: File): List<String> =
        if (!journal.isFile) emptyList()
        else journal.readLines().filter { it.isNotBlank() }.map(PatchProtocol::safeRelativePath)

    private inline fun <T> diagnosticOperation(
        transactionId: String,
        stage: String,
        detail: String = "",
        block: () -> T,
    ): T {
        appendDiagnostic(transactionId, "${stage}_enter", detail)
        return try {
            block().also { appendDiagnostic(transactionId, "${stage}_complete", transactionDetail(transactionRoot(transactionId))) }
        } catch (error: Throwable) {
            val message = buildString {
                append("error=")
                append(error.javaClass.name)
                append(" message=")
                append(error.message.orEmpty())
                append(' ')
                append(transactionDetail(transactionRoot(transactionId)))
            }
            appendDiagnostic(transactionId, "${stage}_failed", message)
            throw error
        }
    }

    private fun resetDiagnostics(transactionId: String, detail: String) {
        runCatching {
            ensureParent(diagnosticsFile)
            FileOutputStream(diagnosticsFile, false).use { output -> output.fd.sync() }
        }
        appendDiagnostic(transactionId, "diagnostics_started", detail)
    }

    private fun appendDiagnostic(transactionId: String, stage: String, detail: String) {
        runCatching {
            ensureParent(diagnosticsFile)
            if (diagnosticsFile.isFile && diagnosticsFile.length() >= MAX_DIAGNOSTIC_FILE_BYTES) return@runCatching
            val event = JSONObject()
                .put("timestampMs", System.currentTimeMillis())
                .put("elapsedRealtimeNanos", android.os.SystemClock.elapsedRealtimeNanos())
                .put("pid", android.os.Process.myPid())
                .put("uid", android.os.Process.myUid())
                .put("thread", Thread.currentThread().name.take(128))
                .put("transactionId", transactionId)
                .put("stage", stage.take(128))
                .put("detail", detail.take(MAX_DIAGNOSTIC_DETAIL_CHARS))
            FileOutputStream(diagnosticsFile, true).use { output ->
                output.write((event.toString() + "\n").toByteArray(Charsets.UTF_8))
                output.fd.sync()
            }
        }
    }

    private fun transactionSnapshot(transaction: File): JSONObject {
        val journal = File(transaction, "journal.txt")
        val catalog = File(transaction, "catalog.txt")
        var journalEntries = -1
        var journalReadError = ""
        runCatching { journalEntries = readJournal(journal).size }
            .onFailure { error -> journalReadError = error.message ?: error.javaClass.simpleName }
        return JSONObject()
            .put("exists", transaction.isDirectory)
            .put("path", transaction.path)
            .put("catalogExists", catalog.isFile)
            .put("catalogBytes", if (catalog.isFile) catalog.length() else 0L)
            .put("journalExists", journal.isFile)
            .put("journalBytes", if (journal.isFile) journal.length() else 0L)
            .put("journalEntries", journalEntries)
            .put("journalReadError", journalReadError)
            .put("previousFiles", countFiles(File(transaction, "previous")))
            .put("stagingFiles", countFiles(File(transaction, "staging")))
    }

    private fun transactionDetail(transaction: File): String =
        runCatching { transactionSnapshot(transaction).toString() }
            .getOrElse { error ->
                JSONObject()
                    .put("snapshotError", (error.message ?: error.javaClass.simpleName).take(512))
                    .toString()
            }

    private fun journalDetail(journal: File): String {
        val count = runCatching { readJournal(journal).size }.getOrElse { -1 }
        return "journalExists=${journal.isFile} journalBytes=${if (journal.isFile) journal.length() else 0L} " +
            "journalEntries=$count"
    }

    private fun countFiles(root: File): Int =
        if (!root.isDirectory) 0 else root.walkTopDown().count { it.isFile }

    private fun readState(): JSONObject? =
        if (!stateFile.isFile) null
        else JSONObject(stateFile.readText()).also {
            require(it.getInt("schemaVersion") == 1) { "지원하지 않는 patch 상태입니다." }
        }

    private fun writeTextAtomic(target: File, value: String) {
        ensureParent(target)
        val temporary = File(target.parentFile, "${target.name}.tmp")
        FileOutputStream(temporary).use { output ->
            output.write(value.toByteArray(Charsets.UTF_8))
            output.flush()
            syncBestEffort(output, operation = "atomic_text")
        }
        require(temporary.readText() == value) { "상태 파일 기록을 확인할 수 없습니다." }
        if (target.exists() && !target.delete()) error("상태 파일을 교체할 수 없습니다.")
        if (!temporary.renameTo(target)) error("상태 파일 저장을 완료할 수 없습니다.")
        require(target.readText() == value) { "상태 파일 저장 결과를 확인할 수 없습니다." }
    }

    private fun safePath(root: File, relativePath: String): File {
        val safe = PatchProtocol.safeRelativePath(relativePath)
        val target = File(root, safe)
        val rootPath = root.canonicalPath
        val targetPath = target.canonicalPath
        require(targetPath.startsWith("$rootPath${File.separator}")) { "허용 경로를 벗어났습니다." }
        return target
    }

    private fun ensureParent(file: File) {
        val parent = file.parentFile ?: return
        require(parent.isDirectory || parent.mkdirs()) { "디렉터리를 만들 수 없습니다: $parent" }
    }

    private fun deleteRecursively(file: File) {
        if (!file.exists()) return
        if (file.isDirectory) file.listFiles()?.forEach(::deleteRecursively)
        file.delete()
    }

    private fun catalogVersion(file: File): String =
        file.name.removePrefix("catalog_").removeSuffix(".hash")

    private fun compareVersions(left: String, right: String): Int {
        val leftParts = left.split('.').map { it.toLongOrNull() ?: 0L }
        val rightParts = right.split('.').map { it.toLongOrNull() ?: 0L }
        for (index in 0 until maxOf(leftParts.size, rightParts.size)) {
            val compared = leftParts.getOrElse(index) { 0L }.compareTo(rightParts.getOrElse(index) { 0L })
            if (compared != 0) return compared
        }
        return 0
    }

    private fun requireTransactionId(value: String): String {
        require(value.matches(Regex("^[0-9a-f-]{36}$"))) { "transaction ID가 올바르지 않습니다." }
        return value
    }

    private fun requireCatalogHash(value: String): String = requireHex(value, 32, "catalog hash")
    private fun requireSha256(value: String): String = requireHex(value, 64, "SHA-256")

    private fun requirePositive(value: Long, label: String): Long {
        require(value > 0) { "$label 값이 올바르지 않습니다." }
        return value
    }

    private fun requireHex(value: String, length: Int, label: String): String {
        val normalized = value.lowercase(Locale.ROOT)
        require(normalized.length == length && normalized.all { it in '0'..'9' || it in 'a'..'f' }) {
            "$label 값이 올바르지 않습니다."
        }
        return normalized
    }

    companion object {
        private const val BUFFER_SIZE = 128 * 1024
        private const val MAX_PATCH_FILES = 2_048
        private const val MAX_INSPECTION_REQUEST_BYTES = 256 * 1024
        private const val MAX_DIAGNOSTIC_EVENTS = 512
        private const val MAX_DIAGNOSTIC_DETAIL_CHARS = 4_096
        private const val MAX_DIAGNOSTIC_FILE_BYTES = 512 * 1024L
        private const val MAX_GAME_APK_FILES = 64
        private const val MAX_INSTALL_OUTPUT_BYTES = 64 * 1024
        private const val MAX_STATUS_OUTPUT_CHARS = 1_024
        private const val INSTALL_TIMEOUT_SECONDS = 300L
        private const val OUTPUT_READER_JOIN_TIMEOUT_MS = 5_000L
        private const val PLAY_STORE_PACKAGE = "com.android.vending"
        private val SAFE_APK_NAME = Regex("^[A-Za-z0-9._-]+\\.apk$")
        private val INSTALL_SESSION_ID = Regex("\\[([0-9]+)]")
    }
}

private fun ByteArray.toHex(): String =
    joinToString(separator = "") { "%02x".format(it.toInt() and 0xff) }
