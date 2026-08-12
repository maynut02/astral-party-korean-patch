pub mod game;
pub mod install;
pub mod protocol;

#[cfg(windows)]
pub use game::discover_windows_installation;
pub use game::{
    CatalogIdentity, GameDetectError, GameInstallation, STEAM_APP_ID, build_installation,
    discover_latest_catalog, find_install_from_libraries, parse_install_dir_from_acf,
    parse_library_folders_vdf,
};
pub use install::{
    InstallError, InstallRoots, InstallSummary, OwnershipManifest, RemoveReport, install_patch,
    remove_patch,
};
pub use protocol::{
    InstallTarget, ManifestFile, PatchManifest, ProtocolError, ReleaseIndex, ReleaseIndexEntry,
};
