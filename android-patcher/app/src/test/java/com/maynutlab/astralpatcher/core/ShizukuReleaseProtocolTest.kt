package com.maynutlab.astralpatcher.core

import org.junit.Assert.assertEquals
import org.junit.Test

class ShizukuReleaseProtocolTest {
    @Test
    fun parsesStableVerifiedApk() {
        val parsed = ShizukuReleaseProtocol.parseLatest(releaseJson())
        assertEquals("v13.6.0", parsed.version)
        assertEquals(1234L, parsed.size)
        assertEquals("a".repeat(64), parsed.sha256)
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsPrerelease() {
        ShizukuReleaseProtocol.parseLatest(releaseJson().replace("\"prerelease\": false", "\"prerelease\": true"))
    }

    @Test(expected = IllegalArgumentException::class)
    fun rejectsForeignApkUrl() {
        ShizukuReleaseProtocol.parseLatest(
            releaseJson().replace("https://github.com/thedjchi/Shizuku", "https://example.test/Shizuku")
        )
    }

    private fun releaseJson(): String = """
        {
          "tag_name": "v13.6.0",
          "draft": false,
          "prerelease": false,
          "assets": [
            {
              "name": "shizuku-v13.6.0-release.apk",
              "browser_download_url": "https://github.com/thedjchi/Shizuku/releases/download/v13.6.0/shizuku.apk",
              "digest": "sha256:${"a".repeat(64)}",
              "size": 1234
            }
          ]
        }
    """.trimIndent()
}
