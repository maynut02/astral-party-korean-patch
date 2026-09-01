package com.maynutlab.astralpatcher.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.MessageDigest

class PatchProtocolTest {
    @Test
    fun resolvesExactAndroidRelease() {
        val catalog = CatalogIdentity("3.2.0", "b".repeat(32), null)
        val release = PatchProtocol.resolveRelease(indexJson(), catalog)
        assertNotNull(release)
        assertEquals("v3.2.0_r116", release?.patchVersion)
        assertNull(PatchProtocol.resolveRelease(indexJson(), catalog.copy(catalogHash = "c".repeat(32))))
    }

    @Test
    fun parsesVerifiedAddressablesManifest() {
        val manifest = manifestJson().toByteArray()
        val release = ReleaseEntry(
            gameVersion = "3.2.0",
            revision = "116",
            catalogHash = "b".repeat(32),
            patchVersion = "v3.2.0_r116",
            manifestUrl = trustedUrl("INT_ANDROID_manifest.json"),
            manifestSha256 = sha256(manifest),
        )
        val parsed = PatchProtocol.parseManifest(manifest, release)
        assertEquals(1, parsed.files.size)
        assertEquals("bundle/${"d".repeat(32)}/__data", parsed.files.single().relativePath)
        assertEquals("1".repeat(64), parsed.files.single().sourceSha256)
        assertEquals("2".repeat(64), parsed.files.single().sourceDownloadSha256)
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsGameDataTargets() {
        val manifest = manifestJson().replace("\"addressables\"", "\"game-data\"").toByteArray()
        PatchProtocol.parseManifest(
            manifest,
            ReleaseEntry(
                "3.2.0",
                "116",
                "b".repeat(32),
                "v3.2.0_r116",
                trustedUrl("INT_ANDROID_manifest.json"),
                sha256(manifest),
            ),
        )
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsTraversalPaths() {
        PatchProtocol.safeRelativePath("../data.unity3d")
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsForeignReleaseHosts() {
        TrustedUrls.requireReleaseAsset("https://example.test/payload.gz")
    }

    @Test
    fun createsAndParsesTargetInspectionProtocol() {
        val bytes = manifestJson().toByteArray()
        val manifest = PatchProtocol.parseManifest(
            bytes,
            ReleaseEntry(
                "3.2.0",
                "116",
                "b".repeat(32),
                "v3.2.0_r116",
                trustedUrl("INT_ANDROID_manifest.json"),
                sha256(bytes),
            ),
        )
        val request = org.json.JSONObject(PatchProtocol.createTargetInspectionRequest(manifest))
        assertEquals("b".repeat(32), request.getString("catalogHash"))
        assertEquals(1, request.getJSONArray("files").length())

        val result = PatchProtocol.parseTargetInspection(
            """{"schemaVersion":1,"total":1,"ready":1,"missing":0,"incompatible":0}"""
        )
        assertTrue(result.isReady)
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsInconsistentTargetInspectionCounts() {
        PatchProtocol.parseTargetInspection(
            """{"schemaVersion":1,"total":2,"ready":1,"missing":0,"incompatible":0}"""
        )
    }

    @Test
    fun createsAndParsesApplyFileResults() {
        val success = PatchProtocol.parseApplyFileResult(PatchProtocol.createApplyFileResult())
        assertTrue(success.ok)
        assertEquals("", success.error)

        val failure = PatchProtocol.parseApplyFileResult(
            PatchProtocol.createApplyFileResult(IllegalStateException("apply failed"))
        )
        assertTrue(!failure.ok)
        assertTrue(failure.error.contains("IllegalStateException"))
        assertTrue(failure.error.contains("apply failed"))
    }

    @Test
    fun parsesPatchDiagnosticsWithJournalSnapshot() {
        val transactionId = "12345678-1234-1234-1234-123456789abc"
        val diagnostics = PatchProtocol.parsePatchDiagnostics(
            """
            {
              "schemaVersion": 1,
              "serviceInfo": "AstralPatchService/3 uid=2000 pid=1234",
              "transactionId": "$transactionId",
              "events": [
                {
                  "timestampMs": 1700000000000,
                  "elapsedRealtimeNanos": 123456789,
                  "pid": 1234,
                  "uid": 2000,
                  "thread": "Binder:1234_1",
                  "transactionId": "$transactionId",
                  "stage": "journal_written",
                  "detail": "journalExists=true journalBytes=80 journalEntries=1"
                }
              ],
              "transaction": {
                "exists": true,
                "path": "/storage/emulated/0/Android/data/game/files/.astral/transactions/$transactionId",
                "catalogExists": true,
                "catalogBytes": 32,
                "journalExists": true,
                "journalBytes": 80,
                "journalEntries": 1,
                "journalReadError": "",
                "previousFiles": 1,
                "stagingFiles": 1
              },
              "diagnosticsFileExists": true,
              "diagnosticsFileBytes": 1024,
              "readError": ""
            }
            """.trimIndent(),
            transactionId,
        )

        assertEquals("journal_written", diagnostics.events.single().stage)
        assertEquals(1234, diagnostics.events.single().pid)
        assertEquals(1, diagnostics.transaction.journalEntries)
        assertTrue(diagnostics.transaction.journalExists)
    }

    private fun indexJson(): String = """
        {
          "schemaVersion": 1,
          "releases": [
            {
              "route": "INT_ANDROID",
              "gameVersion": "3.2.0",
              "revision": "116",
              "catalogHash": "${"b".repeat(32)}",
              "channel": "release",
              "patchVersion": "v3.2.0_r116",
              "manifestUrl": "${trustedUrl("INT_ANDROID_manifest.json")}",
              "manifestSha256": "${"a".repeat(64)}"
            }
          ]
        }
    """.trimIndent()

    private fun manifestJson(): String = """
        {
          "schemaVersion": 2,
          "patch": {
            "version": "v3.2.0_r116",
            "channel": "release",
            "route": "INT_ANDROID",
            "buildId": "build-1",
            "translationFingerprint": "${"c".repeat(64)}"
          },
          "game": {
            "version": "3.2.0",
            "revision": "116",
            "catalogHash": "${"b".repeat(32)}"
          },
          "files": [
            {
              "target": "addressables",
              "path": "bundle/${"d".repeat(32)}/__data",
              "operation": "replace",
              "downloadUrl": "${trustedUrl("payload.gz")}",
              "downloadSha256": "${"e".repeat(64)}",
              "downloadSize": 100,
              "compression": "gzip",
              "sha256": "${"f".repeat(64)}",
              "size": 200,
              "sourceDownloadUrl": "${trustedUrl("original.gz")}",
              "sourceDownloadSha256": "${"2".repeat(64)}",
              "sourceDownloadSize": 90,
              "sourceSha256": "${"1".repeat(64)}",
              "sourceSize": 180
            }
          ]
        }
    """.trimIndent()

    private fun trustedUrl(name: String): String =
        "https://github.com/maynut02/astral-party-korean-patch/releases/download/v3.2.0_r116/$name"

    private fun sha256(data: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(data)
            .joinToString("") { "%02x".format(it.toInt() and 0xff) }
}
