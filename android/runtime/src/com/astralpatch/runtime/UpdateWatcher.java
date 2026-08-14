package com.astralpatch.runtime;

import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.widget.Toast;

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
                        Handler main = new Handler(Looper.getMainLooper());
                        main.post(new Runnable() {
                            @Override
                            public void run() {
                                Toast.makeText(
                                        app,
                                        "한국어 패치를 적용할 준비가 되었습니다. 게임을 완전히 종료한 뒤 다시 실행해 주세요.",
                                        Toast.LENGTH_LONG)
                                        .show();
                            }
                        });
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
