package com.astralpatch.runtime;

import android.content.Context;
import android.content.Intent;

import java.util.concurrent.atomic.AtomicBoolean;

final class UpdateWatcher {
    private static final AtomicBoolean STARTED = new AtomicBoolean(false);

    private UpdateWatcher() {}

    static void start(
            final Context context,
            final RuntimeConfig config,
            final String bootCatalogHash,
            final boolean watchCurrentCatalog) {
        if (!STARTED.compareAndSet(false, true)) {
            return;
        }
        final Context app = context.getApplicationContext();
        Thread watcher = new Thread(new Runnable() {
            @Override
            public void run() {
                boolean sameCatalogNeedsCheck = watchCurrentCatalog;
                while (true) {
                    try {
                        Thread.sleep(config.watcherIntervalMs);
                        String current = PatchEngine.currentCatalogHash(app, config);
                        if (current == null) {
                            continue;
                        }
                        boolean changed = bootCatalogHash == null || !current.equals(bootCatalogHash);
                        if (!changed && !sameCatalogNeedsCheck) {
                            continue;
                        }
                        if (!PatchEngine.canApplyNow(app, config)) {
                            continue;
                        }
                        app.getSharedPreferences("astralpatch", Context.MODE_PRIVATE)
                                .edit()
                                .putBoolean("restartRequired", true)
                                .putString("restartCatalogHash", current)
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
                        // Network or partially downloaded game data may be transient. Retry later.
                    }
                }
            }
        }, "AstralPatchWatcher");
        watcher.setDaemon(true);
        watcher.start();
    }
}
