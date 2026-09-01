package com.maynutlab.astralpatcher;

import android.os.ParcelFileDescriptor;

interface IPatchService {
    String getServiceInfo();
    String inspectGame();
    void beginPatch(String transactionId, String catalogHash);
    void applyFile(
        String transactionId,
        in ParcelFileDescriptor payload,
        long size,
        String sha256,
        long sourceSize,
        String sourceSha256,
        String relativePath
    );
    void commitPatch(
        String transactionId,
        String gameVersion,
        String catalogHash,
        String patchVersion
    );
    void rollbackPatch(String transactionId);
    String restorePatch();
    void forceStopGame();
    void destroy();
}
