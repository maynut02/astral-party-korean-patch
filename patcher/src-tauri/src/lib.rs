mod app;
pub mod game;
pub mod install;
pub mod network;
pub mod protocol;
pub mod service;

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

pub use network::{NetworkError, ReleaseClient};

#[cfg(windows)]
pub use service::install_latest_compatible;
pub use service::{
    DEFAULT_ROUTE, InstallOutcome, InstalledPatchInfo, PatcherPaths, ServiceError, install_roots,
    installed_patch_info, load_ownership, remove_installed_patch,
};

pub use app::run;
