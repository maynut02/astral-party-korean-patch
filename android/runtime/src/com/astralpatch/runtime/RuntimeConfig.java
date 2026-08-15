package com.astralpatch.runtime;

import android.content.Context;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;

final class RuntimeConfig {
    static final String ASSET_PATH = "astralpatch/config.json";

    final String route;
    final String channel;
    final String releaseIndexUrl;
    final String gameActivity;
    final String addressablesDir;
    final String assetBundleCacheDir;

    private RuntimeConfig(
            String route,
            String channel,
            String releaseIndexUrl,
            String gameActivity,
            String addressablesDir,
            String assetBundleCacheDir) {
        this.route = route;
        this.channel = channel;
        this.releaseIndexUrl = releaseIndexUrl;
        this.gameActivity = gameActivity;
        this.addressablesDir = addressablesDir;
        this.assetBundleCacheDir = assetBundleCacheDir;
    }

    static RuntimeConfig load(Context context) throws Exception {
        String raw;
        try (InputStream input = context.getAssets().open(ASSET_PATH)) {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                output.write(buffer, 0, read);
            }
            raw = new String(output.toByteArray(), StandardCharsets.UTF_8);
        }
        JSONObject json = new JSONObject(raw);
        if (json.optInt("schemaVersion", 0) != 1) {
            throw new IllegalStateException("unsupported Astral patch runtime config schema");
        }
        return new RuntimeConfig(
                json.getString("route"),
                json.optString("channel", "release"),
                json.getString("releaseIndexUrl"),
                json.getString("gameActivity"),
                json.optString("addressablesDir", "com.unity.addressables"),
                json.optString("assetBundleCacheDir", "com.unity.addressables/AssetBundles"));
    }
}
