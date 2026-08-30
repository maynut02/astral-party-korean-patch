package io.github.maynut02.astralpatcher;

import android.os.ParcelFileDescriptor;

interface IInstallerService {
    String installGameApk(in ParcelFileDescriptor apk, long size) = 1;
    void destroy() = 16777114;
}
