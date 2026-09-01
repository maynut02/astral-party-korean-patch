package com.maynutlab.astralpatcher.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
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
