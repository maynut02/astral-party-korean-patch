package com.maynutlab.astralpatcher.shizuku

import android.os.ParcelFileDescriptor
import androidx.annotation.Keep
import com.maynutlab.astralpatcher.IPatchService
import com.maynutlab.astralpatcher.core.GAME_PACKAGE
import com.maynutlab.astralpatcher.core.PatchProtocol
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.Locale

@Keep
class PatchUserService : IPatchService.Stub() {
    private val filesRoot = File("/storage/emulated/0/Android/data/$GAME_PACKAGE/files")
    private val addressablesRoot = File(filesRoot, "com.unity.addressables")
    private val bundleRoot = File(addressablesRoot, "AssetBundles")
    private val stateRoot = File(filesRoot, ".astralpatch-manager")
    private val transactionsRoot = File(stateRoot, "transactions")
    private val backupsRoot = File(stateRoot, "backups")
    private val stateFile = File(stateRoot, "state.json")

    override fun getServiceInfo(): String =
        "AstralPatchService/2 uid=${android.os.Process.myUid()} pid=${android.os.Process.myPid()}"

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
        forceStopGame()
        recoverInterruptedTransactions()
        require(discoverCatalog().second == expectedHash) { "게임 catalog가 패치 확인 후 변경되었습니다." }
        val transaction = transactionRoot(safeId)
        require(!transaction.exists()) { "같은 patch transaction이 이미 존재합니다." }
        require(transaction.mkdirs()) { "patch transaction 디렉터리를 만들 수 없습니다." }
        writeTextAtomic(File(transaction, "catalog.txt"), expectedHash)
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
    ) {
        require(size > 0) { "payload 크기가 올바르지 않습니다." }
        require(sourceSize > 0) { "원본 파일 크기가 올바르지 않습니다." }
        val expectedSha = requireSha256(sha256)
        val expectedSourceSha = requireSha256(sourceSha256)
        val safePath = PatchProtocol.safeRelativePath(relativePath)
        val transaction = activeTransaction(transactionId)
        val catalogHash = File(transaction, "catalog.txt").readText().trim()
        val target = resolveExistingBundle(safePath)
        val actualPath = target.relativeTo(bundleRoot).invariantSeparatorsPath
        val journal = File(transaction, "journal.txt")
        val applied = readJournal(journal)
        require(actualPath !in applied) { "같은 파일을 두 번 적용할 수 없습니다: $actualPath" }

        val permanentBackup = File(File(backupsRoot, catalogHash), actualPath)
        if (permanentBackup.isFile) {
            verifyFile(permanentBackup, sourceSize, expectedSourceSha, "보관된 원본")
        } else {
            verifyFile(target, sourceSize, expectedSourceSha, "게임 원본")
            copyVerified(target, permanentBackup)
        }
        val previous = File(transaction, "previous/$actualPath")
        copyVerified(target, previous)

        val staged = File(transaction, "staging/$actualPath")
        receiveVerified(payload, staged, size, expectedSha)
        appendJournal(journal, actualPath)
        replaceFile(staged, target)
        verifyFile(target, size, expectedSha, "적용된 patch")
    }

    @Synchronized
    override fun commitPatch(
        transactionId: String,
        gameVersion: String,
        catalogHash: String,
        patchVersion: String,
    ) {
        require(gameVersion.isNotBlank() && patchVersion.isNotBlank()) { "patch 상태 정보가 비어 있습니다." }
        val expectedHash = requireCatalogHash(catalogHash)
        val transaction = activeTransaction(transactionId)
        require(File(transaction, "catalog.txt").readText().trim() == expectedHash) {
            "transaction catalog가 다릅니다."
        }
        require(readJournal(File(transaction, "journal.txt")).isNotEmpty()) { "적용된 patch 파일이 없습니다." }
        val state = JSONObject()
            .put("schemaVersion", 1)
            .put("gameVersion", gameVersion)
            .put("catalogHash", expectedHash)
            .put("patchVersion", patchVersion)
        writeTextAtomic(stateFile, state.toString(2))
        deleteRecursively(transaction)
    }

    @Synchronized
    override fun rollbackPatch(transactionId: String) {
        val transaction = transactionRoot(requireTransactionId(transactionId))
        if (transaction.isDirectory) rollbackTransaction(transaction)
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
        System.exit(0)
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
    ) {
        ensureParent(target)
        val digest = MessageDigest.getInstance("SHA-256")
        var total = 0L
        ParcelFileDescriptor.AutoCloseInputStream(descriptor).use { raw ->
            BufferedInputStream(raw).use { input ->
                FileOutputStream(target).use { fileOutput ->
                    BufferedOutputStream(fileOutput).use { output ->
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
                    }
                    fileOutput.fd.sync()
                }
            }
        }
        require(total == expectedSize) { "payload stream 크기가 다릅니다." }
        require(digest.digest().toHex() == expectedSha) { "payload stream SHA-256이 다릅니다." }
    }

    private fun copyVerified(source: File, target: File) {
        val size = source.length()
        val hash = sha256(source)
        copyFile(source, target)
        verifyFile(target, size, hash, "backup")
    }

    private fun copyFile(source: File, target: File) {
        ensureParent(target)
        BufferedInputStream(FileInputStream(source)).use { input ->
            FileOutputStream(target).use { fileOutput ->
                BufferedOutputStream(fileOutput).use { output -> input.copyTo(output, BUFFER_SIZE) }
                fileOutput.fd.sync()
            }
        }
    }

    private fun replaceFile(source: File, target: File) {
        ensureParent(target)
        val temporary = File(target.parentFile, "${target.name}.astral.new")
        temporary.delete()
        copyFile(source, temporary)
        if (target.exists() && !target.delete()) {
            temporary.delete()
            error("게임 리소스를 교체할 수 없습니다: $target")
        }
        if (!temporary.renameTo(target)) {
            temporary.delete()
            error("게임 리소스 교체를 완료할 수 없습니다: $target")
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

    private fun appendJournal(journal: File, relativePath: String) {
        ensureParent(journal)
        FileOutputStream(journal, true).use { output ->
            output.write((relativePath + "\n").toByteArray(Charsets.UTF_8))
            output.fd.sync()
        }
    }

    private fun readJournal(journal: File): List<String> =
        if (!journal.isFile) emptyList()
        else journal.readLines().filter { it.isNotBlank() }.map(PatchProtocol::safeRelativePath)

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
            output.fd.sync()
        }
        if (target.exists() && !target.delete()) error("상태 파일을 교체할 수 없습니다.")
        if (!temporary.renameTo(target)) error("상태 파일 저장을 완료할 수 없습니다.")
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
    }
}

private fun ByteArray.toHex(): String =
    joinToString(separator = "") { "%02x".format(it.toInt() and 0xff) }
