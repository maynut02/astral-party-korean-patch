package io.github.maynut02.astralpatcher;

import static org.junit.Assert.assertEquals;

import org.json.JSONException;
import org.junit.Test;

public final class DistributionIndexTest {
    @Test
    public void parsesTrustedIndex() throws Exception {
        String json = """
                {
                  "schemaVersion": 1,
                  "packageName": "com.feimo.astralpartyjpn",
                  "gameVersion": "3.2.0",
                  "downloadUrl": "https://github.com/maynut02/astral-party-korean-patch/releases/download/int-apk-v3.2.0/AstralPartyKorean.apk",
                  "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                  "size": 1234,
                  "installerPackageName": "com.android.vending"
                }
                """;

        DistributionIndex index = DistributionIndex.parse(json);

        assertEquals("3.2.0", index.gameVersion);
        assertEquals(1234, index.size);
    }

    @Test(expected = JSONException.class)
    public void rejectsUntrustedDownloadHost() throws Exception {
        String json = """
                {
                  "schemaVersion": 1,
                  "packageName": "com.feimo.astralpartyjpn",
                  "gameVersion": "3.2.0",
                  "downloadUrl": "https://example.test/AstralPartyKorean.apk",
                  "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                  "size": 1234,
                  "installerPackageName": "com.android.vending"
                }
                """;

        DistributionIndex.parse(json);
    }
}
