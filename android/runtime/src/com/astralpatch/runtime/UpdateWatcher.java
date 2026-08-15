package com.astralpatch.runtime;

import android.content.Context;
import android.content.Intent;
import android.os.FileObserver;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

final class UpdateWatcher {
    private static final AtomicBoolean STARTED = new AtomicBoolean(false);
    private static final long CATALOG_POLL_MS = 1000L;
    private static final int OBSERVER_MASK =
            FileObserver.CREATE
                    | FileObserver.MOVED_TO
                    | FileObserver.CLOSE_WRITE
                    | FileObserver.DELETE
                    | FileObserver.MOVED_FROM
                    | FileObserver.DELETE_SELF
                    | FileObserver.MOVE_SELF;

    private UpdateWatcher() {}

    private static final class BundleObserver {
        private final Object lock = new Object();
        private final String catalogHash;
        private final List<String> logicalPaths;
        private final Map<String, List<File>> candidatesByLogical = new HashMap<>();
        private final Map<String, String> logicalByCanonicalCandidate = new HashMap<>();
        private final Set<String> completedLogicalPaths = new HashSet<>();
        private final Set<String> eventCompletedLogicalPaths = new HashSet<>();
        private final List<FileObserver> observers = new ArrayList<>();
        private boolean signaled;
        private boolean refreshRequired;
        private boolean closed;

        BundleObserver(File bundleRoot, String catalogHash, List<String> logicalPaths)
                throws Exception {
            this.catalogHash = catalogHash;
            this.logicalPaths = Collections.unmodifiableList(new ArrayList<>(logicalPaths));
            for (String logicalPath : this.logicalPaths) {
                List<File> candidates = bundleCandidates(bundleRoot, logicalPath);
                candidatesByLogical.put(logicalPath, candidates);
                for (File candidate : candidates) {
                    logicalByCanonicalCandidate.put(candidate.getCanonicalPath(), logicalPath);
                }
            }
            synchronized (lock) {
                refreshObserversLocked();
                synchronizeCompletedTargetsLocked();
            }
        }

        boolean matches(String hash, List<String> paths) {
            return catalogHash.equals(hash) && logicalPaths.equals(paths);
        }

        boolean isReady() {
            synchronized (lock) {
                synchronizeCompletedTargetsLocked();
                return completedLogicalPaths.size() == logicalPaths.size();
            }
        }

        void awaitChange(long timeoutMs) throws InterruptedException {
            synchronized (lock) {
                if (!signaled && !closed) {
                    lock.wait(timeoutMs);
                }
                signaled = false;
                if (closed) {
                    return;
                }
                if (refreshRequired) {
                    refreshRequired = false;
                    refreshObserversLocked();
                }
                synchronizeCompletedTargetsLocked();
            }
        }

        void close() {
            synchronized (lock) {
                closed = true;
                stopObserversLocked();
                lock.notifyAll();
            }
        }

        private void refreshObserversLocked() {
            stopObserversLocked();
            Set<String> directories = new HashSet<>();
            for (List<File> candidates : candidatesByLogical.values()) {
                for (File candidate : candidates) {
                    File directory = nearestExistingDirectory(candidate.getParentFile());
                    if (directory != null) {
                        directories.add(directory.getAbsolutePath());
                    }
                }
            }
            for (String directoryPath : directories) {
                final File directory = new File(directoryPath);
                FileObserver observer = new FileObserver(directoryPath, OBSERVER_MASK) {
                    @Override
                    public void onEvent(int event, String path) {
                        handleEvent(directory, event, path);
                    }
                };
                observer.startWatching();
                observers.add(observer);
            }
        }

        private void stopObserversLocked() {
            for (FileObserver observer : observers) {
                observer.stopWatching();
            }
            observers.clear();
        }

        private void handleEvent(File directory, int event, String path) {
            synchronized (lock) {
                if (closed) {
                    return;
                }
                if (path != null && (event & (FileObserver.CLOSE_WRITE | FileObserver.MOVED_TO)) != 0) {
                    File changed = new File(directory, path);
                    try {
                        String logical = logicalByCanonicalCandidate.get(changed.getCanonicalPath());
                        if (logical != null && changed.isFile() && changed.length() > 0) {
                            eventCompletedLogicalPaths.add(logical);
                        }
                    } catch (Exception ignored) {
                        // A following synchronization pass will recover from transient path races.
                    }
                }
                if ((event
                                & (FileObserver.CREATE
                                        | FileObserver.MOVED_TO
                                        | FileObserver.DELETE
                                        | FileObserver.MOVED_FROM
                                        | FileObserver.DELETE_SELF
                                        | FileObserver.MOVE_SELF))
                        != 0) {
                    refreshRequired = true;
                }
                signaled = true;
                lock.notifyAll();
            }
        }

        private void synchronizeCompletedTargetsLocked() {
            for (String logicalPath : logicalPaths) {
                File existing = firstExistingCandidate(candidatesByLogical.get(logicalPath));
                if (existing == null || existing.length() <= 0) {
                    completedLogicalPaths.remove(logicalPath);
                    eventCompletedLogicalPaths.remove(logicalPath);
                    continue;
                }

                WriterState writerState = writerState(existing);
                if (writerState == WriterState.WRITABLE) {
                    completedLogicalPaths.remove(logicalPath);
                    continue;
                }
                if (writerState == WriterState.IDLE
                        || eventCompletedLogicalPaths.contains(logicalPath)) {
                    completedLogicalPaths.add(logicalPath);
                } else {
                    completedLogicalPaths.remove(logicalPath);
                }
            }
        }
    }

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
                BundleObserver bundleObserver = null;
                try {
                    while (true) {
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
                            if (bundleObserver != null) {
                                bundleObserver.close();
                                bundleObserver = null;
                            }
                            Thread.sleep(CATALOG_POLL_MS);
                            continue;
                        }

                        if (probe.status == PatchEngine.WatchStatus.NO_CATALOG
                                || probe.status == PatchEngine.WatchStatus.NO_RELEASE
                                || probe.status == PatchEngine.WatchStatus.NO_PATHS) {
                            if (bundleObserver != null) {
                                bundleObserver.close();
                                bundleObserver = null;
                            }
                            Thread.sleep(CATALOG_POLL_MS);
                            continue;
                        }

                        if (bundleObserver == null
                                || !bundleObserver.matches(probe.catalogHash, probe.paths)) {
                            if (bundleObserver != null) {
                                bundleObserver.close();
                            }
                            bundleObserver = new BundleObserver(
                                    PatchEngine.watchBundleRoot(app, config),
                                    probe.catalogHash,
                                    probe.paths);
                        }

                        if (bundleObserver.isReady()) {
                            showRestartRequired(app, probe.catalogHash);
                            return;
                        }

                        // FileObserver wakes this immediately on cache writes/renames. The timeout
                        // only rechecks the catalog and recovers from an OS-level missed event.
                        bundleObserver.awaitChange(CATALOG_POLL_MS);
                    }
                } catch (InterruptedException ignored) {
                    Thread.currentThread().interrupt();
                } catch (Exception ignored) {
                    // Runtime patch watching is best-effort. The next app launch performs a full check.
                } finally {
                    if (bundleObserver != null) {
                        bundleObserver.close();
                    }
                }
            }
        }, "AstralPatchWatcher");
        watcher.setDaemon(true);
        watcher.start();
    }

    private static void showRestartRequired(Context app, String catalogHash) {
        app.getSharedPreferences("astralpatch", Context.MODE_PRIVATE)
                .edit()
                .putBoolean("restartRequired", true)
                .putString("restartCatalogHash", catalogHash)
                .apply();
        Intent restart = new Intent(app, RestartRequiredActivity.class);
        restart.addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK
                        | Intent.FLAG_ACTIVITY_CLEAR_TOP
                        | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        app.startActivity(restart);
    }

    private static List<File> bundleCandidates(File root, String relativePath) {
        List<File> candidates = new ArrayList<>();
        File exact = new File(root, relativePath);
        candidates.add(exact);
        if (relativePath.endsWith("/__data")) {
            candidates.add(new File(root, relativePath + "__"));
        } else if (relativePath.endsWith("/__data__")) {
            candidates.add(new File(root, relativePath.substring(0, relativePath.length() - 2)));
        }
        return candidates;
    }

    private static File nearestExistingDirectory(File directory) {
        File current = directory;
        while (current != null) {
            if (current.isDirectory()) {
                return current;
            }
            current = current.getParentFile();
        }
        return null;
    }

    private static File firstExistingCandidate(List<File> candidates) {
        if (candidates == null) {
            return null;
        }
        for (File candidate : candidates) {
            if (candidate.isFile()) {
                return candidate;
            }
        }
        return null;
    }

    private enum WriterState {
        WRITABLE,
        IDLE,
        UNKNOWN
    }

    private static WriterState writerState(File target) {
        try {
            String targetPath = target.getCanonicalPath();
            File fdRoot = new File("/proc/self/fd");
            File[] descriptors = fdRoot.listFiles();
            if (descriptors == null) {
                return WriterState.UNKNOWN;
            }
            for (File descriptor : descriptors) {
                String openedPath;
                try {
                    openedPath = descriptor.getCanonicalPath();
                } catch (Exception ignored) {
                    continue;
                }
                if (!targetPath.equals(openedPath)) {
                    continue;
                }
                Boolean writable = descriptorIsWritable(descriptor.getName());
                if (writable == null) {
                    return WriterState.UNKNOWN;
                }
                if (writable.booleanValue()) {
                    return WriterState.WRITABLE;
                }
            }
            return WriterState.IDLE;
        } catch (Exception ignored) {
            return WriterState.UNKNOWN;
        }
    }

    private static Boolean descriptorIsWritable(String descriptorName) {
        File info = new File("/proc/self/fdinfo", descriptorName);
        try (BufferedReader reader = new BufferedReader(new FileReader(info))) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (!line.startsWith("flags:")) {
                    continue;
                }
                String raw = line.substring("flags:".length()).trim();
                long flags = Long.parseLong(raw, 8);
                long accessMode = flags & 3L;
                return accessMode == 1L || accessMode == 2L;
            }
        } catch (Exception ignored) {
            return null;
        }
        return null;
    }
}
