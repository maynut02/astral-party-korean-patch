package io.github.maynut02.astralpatcher;

import android.os.ParcelFileDescriptor;
import android.os.RemoteException;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;

public final class InstallerUserService extends IInstallerService.Stub {
    private static final int BUFFER_SIZE = 64 * 1024;
    private static final int MAX_OUTPUT_BYTES = 64 * 1024;
    private static final String SERVICE_INFO = "AstralInstallerService/1";

    public InstallerUserService() {
    }

    @Override
    public String getServiceInfo() {
        return SERVICE_INFO + " uid=" + android.os.Process.myUid();
    }

    @Override
    public String installGameApk(ParcelFileDescriptor apk, long size) throws RemoteException {
        try {
            if (apk == null) {
                throw new IllegalArgumentException("APK file descriptor is missing.");
            }
            if (size <= 0) {
                throw new IllegalArgumentException("APK size must be positive.");
            }

            Process process = new ProcessBuilder(
                    "/system/bin/pm",
                    "install",
                    "-r",
                    "-i",
                    DistributionIndex.INSTALLER_PACKAGE,
                    "--pkg",
                    DistributionIndex.PACKAGE_NAME,
                    "-S",
                    Long.toString(size),
                    "-")
                    .redirectErrorStream(true)
                    .start();

            ByteArrayOutputStream commandOutput = new ByteArrayOutputStream();
            Thread outputReader = new Thread(
                    () -> readCommandOutput(process.getInputStream(), commandOutput),
                    "pm-install-output");
            outputReader.start();

            try (ParcelFileDescriptor.AutoCloseInputStream input =
                         new ParcelFileDescriptor.AutoCloseInputStream(apk);
                 OutputStream output = process.getOutputStream()) {
                byte[] buffer = new byte[BUFFER_SIZE];
                long written = 0;
                int read;
                while ((read = input.read(buffer)) != -1) {
                    output.write(buffer, 0, read);
                    written += read;
                }
                output.flush();
                if (written != size) {
                    process.destroy();
                    throw new IllegalStateException(
                            "APK stream size mismatch: expected " + size + ", wrote " + written);
                }
            }

            int exitCode = process.waitFor();
            outputReader.join();
            String result = new String(commandOutput.toByteArray(), StandardCharsets.UTF_8).trim();
            if (exitCode != 0 || !result.contains("Success")) {
                throw new IllegalStateException(
                        "pm install failed (exit=" + exitCode + "): " + result);
            }
            return result;
        } catch (Exception error) {
            String message = error.getMessage();
            throw new RemoteException(message == null ? error.getClass().getSimpleName() : message);
        }
    }

    private static void readCommandOutput(InputStream input, ByteArrayOutputStream output) {
        byte[] buffer = new byte[4096];
        try (InputStream stream = input) {
            int read;
            while ((read = stream.read(buffer)) != -1) {
                int remaining = MAX_OUTPUT_BYTES - output.size();
                if (remaining > 0) {
                    output.write(buffer, 0, Math.min(read, remaining));
                }
            }
        } catch (Exception ignored) {
            // The install result is validated from the process exit code and captured output.
        }
    }

    @Override
    public void destroy() {
        System.exit(0);
    }
}
