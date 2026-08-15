package com.astralpatch.runtime;

import android.content.Context;
import android.content.Intent;
import android.os.SystemClock;

import java.util.concurrent.atomic.AtomicBoolean;

final class UpdateWatcher {
    private static final AtomicBoolean STARTED = new AtomicBoolean(false);
    private static final long CATALOG_POLL_MS = 1000L;
    private static final long BUNDLE_POLL_MS = 250L;
    private static final long BUNDLE_STABLE_MS = 1000L;

    private UpdateWatcher() {}

    static void start(
            final Context context,
            final RuntimeConfig config,
            final PatchEngine.ReleaseSnapshot snapshot,
            final String bootCatalogVersion,
            final String bootCatalogHash,
            final boolean watchBootCatalog) {
        if (snapshot == null
                || !snapshot.hasWatchTarget(
                        bootCatalogVersion, bootCatalogHash, watchBootCatalog)) {
            return;
        }
        if (!STARTED.compareAndSet(false, true)) {
            return;
        }
        final Context app = context.getApplicationContext();
        Thread watcher = new Thread(new Runnable() {
            @Override
            public void run() {
                String stableKey = null;
                long stableSince = 0L;
                long delayMs = CATALOG_POLL_MS;
                while (true) {
                    try {
                        Thread.sleep(delayMs);
                        PatchEngine.WatchProbe probe = PatchEngine.probeLocalWatch(
                                app,
                                config,
                                snapshot,
                                bootCatalogVersion,
                                bootCatalogHash,
                                watchBootCatalog);
                        boolean changed = bootCatalogHash == null
                                || (probe.catalogHash != null
                                        && !probe.catalogHash.equals(bootCatalogHash));
                        if (!changed && !watchBootCatalog) {
                            stableKey = null;
                            stableSince = 0L;
                            delayMs = CATALOG_POLL_MS;
                            continue;
                        }

                        if (probe.status != PatchEngine.WatchStatus.READY) {
                            stableKey = null;
                            stableSince = 0L;
                            delayMs = probe.status == PatchEngine.WatchStatus.WAITING
                                    ? BUNDLE_POLL_MS
                                    : CATALOG_POLL_MS;
                            continue;
                        }

                        String key = probe.catalogHash + "\n" + probe.fingerprint;
                        long now = SystemClock.elapsedRealtime();
                        if (!key.equals(stableKey)) {
                            stableKey = key;
                            stableSince = now;
                            delayMs = BUNDLE_POLL_MS;
                            continue;
                        }
                        if (now - stableSince < BUNDLE_STABLE_MS) {
                            delayMs = BUNDLE_POLL_MS;
                            continue;
                        }

                        app.getSharedPreferences("astralpatch", Context.MODE_PRIVATE)
                                .edit()
                                .putBoolean("restartRequired", true)
                                .putString("restartCatalogHash", probe.catalogHash)
                                .apply();
                        Intent restart = new Intent(app, RestartRequiredActivity.class);
                        restart.addFlags(
                                Intent.FLAG_ACTIVITY_NEW_TASK
                                        | Intent.FLAG_ACTIVITY_CLEAR_TOP
                                        | Intent.FLAG_ACTIVITY_SINGLE_TOP);
                        app.startActivity(restart);
                        return;
                    } catch (InterruptedException ignored) {
                        Thread.currentThread().interrupt();
                        return;
                    } catch (Exception ignored) {
                        stableKey = null;
                        stableSince = 0L;
                        delayMs = CATALOG_POLL_MS;
                    }
                }
            }
        }, "AstralPatchWatcher");
        watcher.setDaemon(true);
        watcher.start();
    }
}
