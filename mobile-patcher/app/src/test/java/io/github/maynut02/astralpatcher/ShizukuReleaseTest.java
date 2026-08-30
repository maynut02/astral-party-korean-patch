package io.github.maynut02.astralpatcher;

import static org.junit.Assert.assertEquals;

import org.json.JSONException;
import org.junit.Test;

public final class ShizukuReleaseTest {
    @Test
    public void selectsStableApkAndDigest() throws Exception {
        String json = """
                {
                  "tag_name": "v13.6.0.r1318-thedjchi",
                  "draft": false,
                  "prerelease": false,
                  "assets": [
                    {
                      "name": "shizuku-v13.6.0.r1318-thedjchi.apk",
                      "browser_download_url": "https://github.com/thedjchi/Shizuku/releases/download/v13.6.0.r1318-thedjchi/shizuku-v13.6.0.r1318-thedjchi.apk",
                      "digest": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                      "size": 5678
                    }
                  ]
                }
                """;

        ShizukuRelease release = ShizukuRelease.parseLatest(json);

        assertEquals("v13.6.0.r1318-thedjchi", release.version);
        assertEquals("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", release.sha256);
        assertEquals(5678, release.size);
    }

    @Test(expected = JSONException.class)
    public void rejectsPrerelease() throws Exception {
        String json = """
                {
                  "tag_name": "v13.6.0-beta",
                  "draft": false,
                  "prerelease": true,
                  "assets": []
                }
                """;

        ShizukuRelease.parseLatest(json);
    }
}
