package io.github.maynut02.astralpatcher;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Locale;

final class HttpClient {
    interface ProgressListener {
        void onProgress(int percent);
    }

    private static final String USER_AGENT = "AstralMobilePatcher/0.1";
    private static final int MAX_TEXT_RESPONSE_BYTES = 1024 * 1024;

    private HttpClient() {}

    static String getText(String url) throws IOException {
        HttpURLConnection connection = open(url);
        try {
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                throw new IOException("HTTP " + status + " 응답을 받았습니다.");
            }
            try (InputStream input = connection.getInputStream();
                 ByteArrayOutputStream output = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[16 * 1024];
                int read;
                while ((read = input.read(buffer)) != -1) {
                    if (output.size() + read > MAX_TEXT_RESPONSE_BYTES) {
                        throw new IOException("JSON 응답이 허용된 크기를 초과했습니다.");
                    }
                    output.write(buffer, 0, read);
                }
                return new String(output.toByteArray(), StandardCharsets.UTF_8);
            }
        } finally {
            connection.disconnect();
        }
    }

    static String download(File destination, String url, long expectedSize, ProgressListener listener)
            throws IOException {
        HttpURLConnection connection = open(url);
        try {
            int status = connection.getResponseCode();
            if (status < 200 || status >= 300) {
                throw new IOException("다운로드 요청이 HTTP " + status + "로 실패했습니다.");
            }

            long responseSize = connection.getContentLengthLong();
            if (expectedSize > 0 && responseSize > 0 && responseSize != expectedSize) {
                throw new IOException("서버의 Content-Length가 배포 정보와 일치하지 않습니다.");
            }

            File parent = destination.getParentFile();
            if (parent != null && !parent.exists() && !parent.mkdirs()) {
                throw new IOException("다운로드 디렉터리를 만들 수 없습니다.");
            }

            MessageDigest digest;
            try {
                digest = MessageDigest.getInstance("SHA-256");
            } catch (NoSuchAlgorithmException error) {
                throw new IOException("SHA-256을 사용할 수 없습니다.", error);
            }

            long copied = 0;
            byte[] buffer = new byte[64 * 1024];
            try (InputStream input = connection.getInputStream();
                 FileOutputStream output = new FileOutputStream(destination)) {
                int read;
                while ((read = input.read(buffer)) != -1) {
                    output.write(buffer, 0, read);
                    digest.update(buffer, 0, read);
                    copied += read;
                    if (listener != null && expectedSize > 0) {
                        listener.onProgress((int) Math.min(100, copied * 100 / expectedSize));
                    }
                }
            }

            if (expectedSize > 0 && copied != expectedSize) {
                destination.delete();
                throw new IOException("다운로드한 APK 크기가 배포 정보와 일치하지 않습니다.");
            }
            return toHex(digest.digest());
        } finally {
            connection.disconnect();
        }
    }

    private static HttpURLConnection open(String url) throws IOException {
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setConnectTimeout(15_000);
        connection.setReadTimeout(60_000);
        connection.setInstanceFollowRedirects(true);
        connection.setRequestProperty("Accept", "application/vnd.github+json, application/json");
        connection.setRequestProperty("User-Agent", USER_AGENT);
        return connection;
    }

    private static String toHex(byte[] bytes) {
        StringBuilder builder = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) {
            builder.append(String.format(Locale.ROOT, "%02x", value & 0xff));
        }
        return builder.toString();
    }
}
