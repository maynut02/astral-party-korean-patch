package com.astralpatch.runtime;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.zip.GZIPInputStream;

final class PatchEngine {
    private static final int BUFFER_SIZE = 128 * 1024;
    private static final int METADATA_LIMIT = 4 * 1024 * 1024;
    private static final int CONNECT_TIMEOUT_MS = 15000;
    private static final int READ_TIMEOUT_MS = 30000;

    enum Status {
        NO_CATALOG,
        NO_PATCH,
        WAITING_FOR_GAME_DOWNLOAD,
        ALREADY_PATCHED,
        PATCHED
    }

    static final class Result {
        final Status status;
        final String catalogHash;
        final String patchVersion;

        Result(Status status, String catalogHash, String patchVersion) {
            this.status = status;
            this.catalogHash = catalogHash;
            this.patchVersion = patchVersion;
        }
    }

    interface Progress {
        void update(String message);
    }

    private static final class Catalog {
        final String version;
        final String hash;
        final File root;

        Catalog(String version, String hash, File root) {
            this.version = version;
            this.hash = hash;
            this.root = root;
        }
    }

    private static final class Release {
        final String route;
        final String gameVersion;
        final String revision;
        final String catalogHash;
        final String channel;
        final String patchVersion;
        final String manifestUrl;
        final String manifestSha256;

        Release(JSONObject json) throws Exception {
            route = json.getString("route");
            gameVersion = json.getString("gameVersion");
            revision = json.getString("revision");
            catalogHash = json.getString("catalogHash").toLowerCase(Locale.ROOT);
            channel = json.getString("channel");
            patchVersion = json.getString("patchVersion");
            manifestUrl = json.getString("manifestUrl");
            manifestSha256 = json.getString("manifestSha256").toLowerCase(Locale.ROOT);
        }
    }

    private static final class ManifestFile {
        final String path;
        final String downloadUrl;
        final String downloadSha256;
        final long downloadSize;
        final String sha256;
        final long size;
        final String sourceSha256;
        final long sourceSize;

        ManifestFile(JSONObject json) throws Exception {
            path = safeRelativePath(json.getString("path"));
            downloadUrl = json.getString("downloadUrl");
            downloadSha256 = requireSha256(json.getString("downloadSha256"));
            downloadSize = json.getLong("downloadSize");
            sha256 = requireSha256(json.getString("sha256"));
            size = json.getLong("size");
            sourceSha256 = requireSha256(json.getString("sourceSha256"));
            sourceSize = json.getLong("sourceSize");
            if (!"gzip".equals(json.getString("compression"))) {
                throw new IllegalStateException("unsupported patch compression");
            }
            if (downloadSize <= 0 || size <= 0 || sourceSize <= 0) {
                throw new IllegalStateException("invalid patch file size");
            }
        }
    }

    private static final class Manifest {
        final String patchVersion;
        final String route;
        final String channel;
        final String gameVersion;
        final String revision;
        final String catalogHash;
        final List<ManifestFile> files;

        Manifest(byte[] raw) throws Exception {
            JSONObject root = new JSONObject(new String(raw, StandardCharsets.UTF_8));
            if (root.optInt("schemaVersion", 0) != 2) {
                throw new IllegalStateException("unsupported patch manifest schema");
            }
            JSONObject patch = root.getJSONObject("patch");
            JSONObject game = root.getJSONObject("game");
            patchVersion = patch.getString("version");
            route = patch.getString("route");
            channel = patch.getString("channel");
            gameVersion = game.getString("version");
            revision = game.getString("revision");
            catalogHash = game.getString("catalogHash").toLowerCase(Locale.ROOT);
            JSONArray rawFiles = root.getJSONArray("files");
            List<ManifestFile> parsed = new ArrayList<>();
            for (int i = 0; i < rawFiles.length(); i++) {
                JSONObject item = rawFiles.getJSONObject(i);
                if ("addressables".equals(item.optString("target"))) {
                    parsed.add(new ManifestFile(item));
                }
            }
            if (parsed.isEmpty()) {
                throw new IllegalStateException("Android patch has no Addressables payloads");
            }
            files = Collections.unmodifiableList(parsed);
        }
    }

    private PatchEngine() {}

    static Result prepare(Context context, RuntimeConfig config, Progress progress) throws Exception {
        Catalog catalog = discoverCatalog(context, config);
        if (catalog == null) {
            return new Result(Status.NO_CATALOG, null, null);
        }
        progress.update("현재 리소스 버전을 확인하는 중입니다...");
        Release release = resolveRelease(config, catalog);
        if (release == null) {
            return new Result(Status.NO_PATCH, catalog.hash, null);
        }
        Manifest manifest = fetchManifest(config, catalog, release);
        File stateRoot = stateRoot(context);
        File stateFile = new File(stateRoot, "state.json");

        if (allFilesMatch(catalog.root, manifest.files, false)) {
            writeState(stateFile, catalog, manifest);
            return new Result(Status.ALREADY_PATCHED, catalog.hash, manifest.patchVersion);
        }
        if (!allFilesMatch(catalog.root, manifest.files, true)) {
            if (!restoreInterruptedTransaction(stateRoot, catalog, manifest)) {
                return new Result(Status.WAITING_FOR_GAME_DOWNLOAD, catalog.hash, manifest.patchVersion);
            }
            if (!allFilesMatch(catalog.root, manifest.files, true)) {
                return new Result(Status.WAITING_FOR_GAME_DOWNLOAD, catalog.hash, manifest.patchVersion);
            }
        }

        progress.update("한글패치 파일을 준비하는 중입니다...");
        apply(context, catalog, manifest, stateRoot, progress);
        writeState(stateFile, catalog, manifest);
        return new Result(Status.PATCHED, catalog.hash, manifest.patchVersion);
    }

    static boolean canApplyNow(Context context, RuntimeConfig config) throws Exception {
        Catalog catalog = discoverCatalog(context, config);
        if (catalog == null) {
            return false;
        }
        Release release = resolveRelease(config, catalog);
        if (release == null) {
            return false;
        }
        Manifest manifest = fetchManifest(config, catalog, release);
        return allFilesMatch(catalog.root, manifest.files, true);
    }

    static String currentCatalogHash(Context context, RuntimeConfig config) {
        try {
            Catalog catalog = discoverCatalog(context, config);
            return catalog == null ? null : catalog.hash;
        } catch (Exception ignored) {
            return null;
        }
    }

    private static Catalog discoverCatalog(Context context, RuntimeConfig config) throws Exception {
        File filesRoot = context.getExternalFilesDir(null);
        if (filesRoot == null) {
            return null;
        }
        File root = new File(filesRoot, config.addressablesDir);
        File[] entries = root.listFiles();
        if (entries == null) {
            return null;
        }
        List<File> hashes = new ArrayList<>();
        for (File entry : entries) {
            String name = entry.getName();
            if (entry.isFile() && name.startsWith("catalog_") && name.endsWith(".hash")) {
                hashes.add(entry);
            }
        }
        if (hashes.isEmpty()) {
            return null;
        }
        Collections.sort(hashes, new Comparator<File>() {
            @Override
            public int compare(File left, File right) {
                return compareVersions(catalogVersion(left), catalogVersion(right));
            }
        });
        File selected = hashes.get(hashes.size() - 1);
        String version = catalogVersion(selected);
        String hash = new String(readLimited(selected, 1024), StandardCharsets.UTF_8)
                .trim().toLowerCase(Locale.ROOT);
        if (hash.length() != 32 || !isHex(hash)) {
            throw new IllegalStateException("invalid Addressables catalog hash");
        }
        return new Catalog(version, hash, root);
    }

    private static String catalogVersion(File file) {
        String name = file.getName();
        return name.substring("catalog_".length(), name.length() - ".hash".length());
    }

    private static int compareVersions(String left, String right) {
        String[] a = left.split("\\.");
        String[] b = right.split("\\.");
        int count = Math.max(a.length, b.length);
        for (int i = 0; i < count; i++) {
            int av = i < a.length ? parseVersionPart(a[i]) : 0;
            int bv = i < b.length ? parseVersionPart(b[i]) : 0;
            if (av != bv) {
                return av < bv ? -1 : 1;
            }
        }
        return left.compareTo(right);
    }

    private static int parseVersionPart(String value) {
        int result = 0;
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (c < '0' || c > '9') {
                break;
            }
            result = result * 10 + (c - '0');
        }
        return result;
    }

    private static Release resolveRelease(RuntimeConfig config, Catalog catalog) throws Exception {
        byte[] raw = httpGetBytes(config.releaseIndexUrl, METADATA_LIMIT);
        JSONObject root = new JSONObject(new String(raw, StandardCharsets.UTF_8));
        if (root.optInt("schemaVersion", 0) != 1) {
            throw new IllegalStateException("unsupported release index schema");
        }
        JSONArray releases = root.optJSONArray("releases");
        if (releases == null) {
            return null;
        }
        for (int i = 0; i < releases.length(); i++) {
            Release release = new Release(releases.getJSONObject(i));
            if (config.route.equals(release.route)
                    && config.channel.equals(release.channel)
                    && catalog.version.equals(release.gameVersion)
                    && catalog.hash.equals(release.catalogHash)) {
                return release;
            }
        }
        return null;
    }

    private static Manifest fetchManifest(RuntimeConfig config, Catalog catalog, Release release)
            throws Exception {
        byte[] raw = httpGetBytes(release.manifestUrl, METADATA_LIMIT);
        if (!sha256(raw).equals(release.manifestSha256)) {
            throw new IllegalStateException("patch manifest SHA-256 mismatch");
        }
        Manifest manifest = new Manifest(raw);
        if (!config.route.equals(manifest.route)
                || !config.channel.equals(manifest.channel)
                || !release.patchVersion.equals(manifest.patchVersion)
                || !release.revision.equals(manifest.revision)
                || !catalog.version.equals(manifest.gameVersion)
                || !catalog.hash.equals(manifest.catalogHash)) {
            throw new IllegalStateException("patch manifest is incompatible with current game data");
        }
        return manifest;
    }

    private static void apply(
            Context context,
            Catalog catalog,
            Manifest manifest,
            File stateRoot,
            Progress progress) throws Exception {
        File stagingRoot = new File(stateRoot, "staging/" + catalog.hash);
        File backupRoot = new File(stateRoot, "backups/" + catalog.hash);
        deleteRecursively(stagingRoot);
        if (!stagingRoot.mkdirs() && !stagingRoot.isDirectory()) {
            throw new IllegalStateException("failed to create Android patch staging directory");
        }
        if (!backupRoot.mkdirs() && !backupRoot.isDirectory()) {
            throw new IllegalStateException("failed to create Android patch backup directory");
        }

        List<File> staged = new ArrayList<>();
        for (int i = 0; i < manifest.files.size(); i++) {
            ManifestFile item = manifest.files.get(i);
            progress.update("패치 다운로드 중... " + (i + 1) + "/" + manifest.files.size());
            File transport = new File(stagingRoot, item.path + ".gz");
            File payload = new File(stagingRoot, item.path);
            ensureParent(transport);
            downloadTo(item.downloadUrl, transport);
            verifyFile(transport, item.downloadSize, item.downloadSha256, "download");
            gunzip(transport, payload);
            verifyFile(payload, item.size, item.sha256, "payload");
            staged.add(payload);
        }

        List<ManifestFile> backedUp = new ArrayList<>();
        for (ManifestFile item : manifest.files) {
            File source = new File(catalog.root, item.path);
            verifyFile(source, item.sourceSize, item.sourceSha256, "source");
            File backup = new File(backupRoot, item.path);
            ensureParent(backup);
            copyFile(source, backup);
            verifyFile(backup, item.sourceSize, item.sourceSha256, "backup");
            backedUp.add(item);
        }

        try {
            for (int i = 0; i < manifest.files.size(); i++) {
                ManifestFile item = manifest.files.get(i);
                progress.update("한글패치 적용 중... " + (i + 1) + "/" + manifest.files.size());
                File target = new File(catalog.root, item.path);
                replaceFile(staged.get(i), target);
                verifyFile(target, item.size, item.sha256, "installed");
            }
        } catch (Exception error) {
            for (ManifestFile item : backedUp) {
                File backup = new File(backupRoot, item.path);
                File target = new File(catalog.root, item.path);
                if (backup.isFile()) {
                    try {
                        replaceFile(backup, target);
                    } catch (Exception ignored) {
                        // Preserve the original failure; the backup remains for the next boot.
                    }
                }
            }
            throw error;
        } finally {
            deleteRecursively(stagingRoot);
        }
    }

    private static boolean restoreInterruptedTransaction(
            File stateRoot, Catalog catalog, Manifest manifest) throws Exception {
        File backupRoot = new File(stateRoot, "backups/" + catalog.hash);
        boolean found = false;
        for (ManifestFile item : manifest.files) {
            File backup = new File(backupRoot, item.path);
            if (backup.isFile()
                    && backup.length() == item.sourceSize
                    && sha256(backup).equals(item.sourceSha256)) {
                found = true;
            } else {
                return false;
            }
        }
        if (!found) {
            return false;
        }
        for (ManifestFile item : manifest.files) {
            replaceFile(new File(backupRoot, item.path), new File(catalog.root, item.path));
        }
        return true;
    }

    private static boolean allFilesMatch(
            File root, List<ManifestFile> files, boolean source) throws Exception {
        for (ManifestFile item : files) {
            File file = new File(root, item.path);
            long size = source ? item.sourceSize : item.size;
            String hash = source ? item.sourceSha256 : item.sha256;
            if (!file.isFile() || file.length() != size || !sha256(file).equals(hash)) {
                return false;
            }
        }
        return true;
    }

    private static File stateRoot(Context context) {
        File external = context.getExternalFilesDir(null);
        if (external == null) {
            return new File(context.getFilesDir(), "astralpatch");
        }
        return new File(external, ".astralpatch");
    }

    private static void writeState(File stateFile, Catalog catalog, Manifest manifest) throws Exception {
        ensureParent(stateFile);
        JSONObject json = new JSONObject();
        json.put("schemaVersion", 1);
        json.put("gameVersion", catalog.version);
        json.put("catalogHash", catalog.hash);
        json.put("patchVersion", manifest.patchVersion);
        File temp = new File(stateFile.getParentFile(), stateFile.getName() + ".tmp");
        try (FileOutputStream output = new FileOutputStream(temp)) {
            output.write(json.toString(2).getBytes(StandardCharsets.UTF_8));
            output.getFD().sync();
        }
        if (stateFile.exists() && !stateFile.delete()) {
            throw new IllegalStateException("failed to replace Android patch state");
        }
        if (!temp.renameTo(stateFile)) {
            throw new IllegalStateException("failed to commit Android patch state");
        }
    }

    private static String safeRelativePath(String value) {
        String normalized = value.replace('\\', '/');
        if (normalized.length() == 0 || normalized.startsWith("/")) {
            throw new IllegalArgumentException("unsafe Addressables path");
        }
        String[] parts = normalized.split("/");
        for (String part : parts) {
            if (part.length() == 0 || ".".equals(part) || "..".equals(part)) {
                throw new IllegalArgumentException("unsafe Addressables path");
            }
        }
        return normalized;
    }

    private static String requireSha256(String value) {
        String result = value.toLowerCase(Locale.ROOT);
        if (result.length() != 64 || !isHex(result)) {
            throw new IllegalArgumentException("invalid SHA-256");
        }
        return result;
    }

    private static boolean isHex(String value) {
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) {
                return false;
            }
        }
        return true;
    }

    private static byte[] httpGetBytes(String url, int maxBytes) throws Exception {
        HttpURLConnection connection = open(url);
        try (InputStream input = new BufferedInputStream(connection.getInputStream())) {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int total = 0;
            int read;
            while ((read = input.read(buffer)) >= 0) {
                total += read;
                if (total > maxBytes) {
                    throw new IllegalStateException("patch metadata is too large");
                }
                output.write(buffer, 0, read);
            }
            return output.toByteArray();
        } finally {
            connection.disconnect();
        }
    }

    private static void downloadTo(String url, File target) throws Exception {
        ensureParent(target);
        HttpURLConnection connection = open(url);
        try (InputStream input = new BufferedInputStream(connection.getInputStream());
             FileOutputStream fileOutput = new FileOutputStream(target);
             BufferedOutputStream output = new BufferedOutputStream(fileOutput)) {
            byte[] buffer = new byte[BUFFER_SIZE];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                output.write(buffer, 0, read);
            }
            output.flush();
            fileOutput.getFD().sync();
        } finally {
            connection.disconnect();
        }
    }

    private static HttpURLConnection open(String value) throws Exception {
        URL url = new URL(value);
        if (!"https".equalsIgnoreCase(url.getProtocol())) {
            throw new IllegalArgumentException("patch downloads require HTTPS");
        }
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
        connection.setReadTimeout(READ_TIMEOUT_MS);
        connection.setInstanceFollowRedirects(true);
        connection.setRequestProperty("User-Agent", "AstralPatchRuntime/1");
        int status = connection.getResponseCode();
        if (status < 200 || status >= 300) {
            connection.disconnect();
            throw new IllegalStateException("HTTP " + status + " while downloading patch data");
        }
        return connection;
    }

    private static void gunzip(File source, File target) throws Exception {
        ensureParent(target);
        try (GZIPInputStream input = new GZIPInputStream(new BufferedInputStream(new FileInputStream(source)));
             FileOutputStream fileOutput = new FileOutputStream(target);
             BufferedOutputStream output = new BufferedOutputStream(fileOutput)) {
            byte[] buffer = new byte[BUFFER_SIZE];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                output.write(buffer, 0, read);
            }
            output.flush();
            fileOutput.getFD().sync();
        }
    }

    private static void verifyFile(File file, long expectedSize, String expectedSha, String label)
            throws Exception {
        if (!file.isFile()) {
            throw new IllegalStateException(label + " file is missing: " + file);
        }
        if (file.length() != expectedSize) {
            throw new IllegalStateException(label + " file size mismatch: " + file);
        }
        if (!sha256(file).equals(expectedSha)) {
            throw new IllegalStateException(label + " file SHA-256 mismatch: " + file);
        }
    }

    private static String sha256(File file) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new BufferedInputStream(new FileInputStream(file))) {
            byte[] buffer = new byte[BUFFER_SIZE];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                digest.update(buffer, 0, read);
            }
        }
        return hex(digest.digest());
    }

    private static String sha256(byte[] data) throws Exception {
        return hex(MessageDigest.getInstance("SHA-256").digest(data));
    }

    private static String hex(byte[] data) {
        StringBuilder builder = new StringBuilder(data.length * 2);
        for (byte value : data) {
            builder.append(String.format(Locale.ROOT, "%02x", value & 0xff));
        }
        return builder.toString();
    }

    private static byte[] readLimited(File file, int maxBytes) throws Exception {
        if (file.length() > maxBytes) {
            throw new IllegalStateException("file is unexpectedly large: " + file);
        }
        try (InputStream input = new FileInputStream(file)) {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] buffer = new byte[1024];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                output.write(buffer, 0, read);
            }
            return output.toByteArray();
        }
    }

    private static void replaceFile(File source, File target) throws Exception {
        ensureParent(target);
        File temp = new File(target.getParentFile(), target.getName() + ".astral.new");
        if (temp.exists() && !temp.delete()) {
            throw new IllegalStateException("failed to clear temporary patch file");
        }
        copyFile(source, temp);
        if (target.exists() && !target.delete()) {
            temp.delete();
            throw new IllegalStateException("failed to replace game resource: " + target);
        }
        if (!temp.renameTo(target)) {
            temp.delete();
            throw new IllegalStateException("failed to commit game resource: " + target);
        }
    }

    private static void copyFile(File source, File target) throws Exception {
        ensureParent(target);
        try (InputStream input = new BufferedInputStream(new FileInputStream(source));
             FileOutputStream fileOutput = new FileOutputStream(target);
             BufferedOutputStream output = new BufferedOutputStream(fileOutput)) {
            byte[] buffer = new byte[BUFFER_SIZE];
            int read;
            while ((read = input.read(buffer)) >= 0) {
                output.write(buffer, 0, read);
            }
            output.flush();
            fileOutput.getFD().sync();
        }
    }

    private static void ensureParent(File file) {
        File parent = file.getParentFile();
        if (parent != null && !parent.mkdirs() && !parent.isDirectory()) {
            throw new IllegalStateException("failed to create directory: " + parent);
        }
    }

    private static void deleteRecursively(File file) {
        if (file == null || !file.exists()) {
            return;
        }
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children != null) {
                for (File child : children) {
                    deleteRecursively(child);
                }
            }
        }
        file.delete();
    }
}
