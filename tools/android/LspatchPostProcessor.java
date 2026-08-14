import com.android.tools.build.apkzlib.sign.SigningExtension;
import com.android.tools.build.apkzlib.sign.SigningOptions;
import com.android.tools.build.apkzlib.zip.AlignmentRules;
import com.android.tools.build.apkzlib.zip.ZFile;
import com.android.tools.build.apkzlib.zip.ZFileOptions;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.security.cert.X509Certificate;

/**
 * Replaces AstralPatch-owned entries in an existing LSPatch APK without flattening
 * its nested origin.apk/file-link layout, then signs the result with the supplied key.
 */
public final class LspatchPostProcessor {
    private static final String ORIGINAL_APK_ASSET_PATH = "assets/lspatch/origin.apk";
    private static final String CONFIG_ASSET_PATH = "assets/astralpatch/config.json";
    private static final ZFileOptions Z_FILE_OPTIONS = new ZFileOptions().setAlignmentRule(
            AlignmentRules.compose(
                    AlignmentRules.constantForSuffix(".so", 4096),
                    AlignmentRules.constantForSuffix(ORIGINAL_APK_ASSET_PATH, 4096)));

    private LspatchPostProcessor() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 10) {
            throw new IllegalArgumentException(
                    "expected: <apk> <manifest> <unity> <runtime-dex> <dex-name> <config-json> "
                            + "<keystore> <alias> <ks-pass-env> <key-pass-env>");
        }

        File apk = new File(args[0]).getAbsoluteFile();
        Path manifest = Path.of(args[1]);
        Path unity = Path.of(args[2]);
        Path runtimeDex = Path.of(args[3]);
        String dexName = args[4];
        Path configJson = Path.of(args[5]);
        File keystoreFile = new File(args[6]).getAbsoluteFile();
        String alias = args[7];
        String storePassword = requireEnv(args[8]);
        String keyPassword = requireEnv(args[9]);

        try (ZFile zFile = ZFile.openReadWrite(apk, Z_FILE_OPTIONS)) {
            if (zFile.get(ORIGINAL_APK_ASSET_PATH) == null) {
                throw new IllegalArgumentException(
                        "input APK is not an LSPatch APK: missing " + ORIGINAL_APK_ASSET_PATH);
            }

            KeyStore keyStore = KeyStore.getInstance(KeyStore.getDefaultType());
            try (FileInputStream input = new FileInputStream(keystoreFile)) {
                keyStore.load(input, storePassword.toCharArray());
            }
            KeyStore.PrivateKeyEntry keyEntry = (KeyStore.PrivateKeyEntry) keyStore.getEntry(
                    alias, new KeyStore.PasswordProtection(keyPassword.toCharArray()));
            if (keyEntry == null) {
                throw new IllegalArgumentException("keystore alias not found: " + alias);
            }

            new SigningExtension(
                            SigningOptions.builder()
                                    .setMinSdkVersion(28)
                                    .setV2SigningEnabled(true)
                                    .setCertificates((X509Certificate[]) keyEntry.getCertificateChain())
                                    .setKey(keyEntry.getPrivateKey())
                                    .build())
                    .register(zFile);

            try (FileInputStream input = new FileInputStream(manifest.toFile())) {
                zFile.add("AndroidManifest.xml", input);
            }
            try (FileInputStream input = new FileInputStream(unity.toFile())) {
                // Unity data assets are kept uncompressed, matching the APK build layout.
                zFile.add("assets/bin/Data/data.unity3d", input, false);
            }
            try (FileInputStream input = new FileInputStream(runtimeDex.toFile())) {
                zFile.add(dexName, input);
            }
            byte[] config = Files.readAllBytes(configJson);
            try (ByteArrayInputStream input = new ByteArrayInputStream(config)) {
                zFile.add(CONFIG_ASSET_PATH, input);
            }
            zFile.realign();
        }
    }

    private static String requireEnv(String name) {
        String value = System.getenv(name);
        if (value == null || value.isEmpty()) {
            throw new IllegalArgumentException("missing environment variable: " + name);
        }
        return value;
    }
}
