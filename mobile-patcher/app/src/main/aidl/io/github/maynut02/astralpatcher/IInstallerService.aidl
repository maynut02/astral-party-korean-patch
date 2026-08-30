package io.github.maynut02.astralpatcher;

interface IInstallerService {
    String getServiceInfo() = 1;
    void beginInstall(long size) = 2;
    void writeInstallChunk(in byte[] data) = 3;
    String finishInstall() = 4;
    void cancelInstall() = 5;
    void finishInstallV3() = 6;
    void destroy() = 16777114;
}
