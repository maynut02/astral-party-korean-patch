pub mod cli;
pub mod game;
pub mod install;
pub mod network;
pub mod protocol;
pub mod registration;
pub mod service;
pub mod settings;
pub mod tui;
pub mod updater;
pub mod uri;

pub use game::{
    ADDRESSABLES_DIR, CatalogIdentity, GameDetectError, GameInstallation, LOCALLOW_GAME_RELATIVE,
    STEAM_APP_ID, build_installation, discover_latest_catalog, find_install_from_libraries,
    normalize_locallow_root, normalize_steam_root, parse_install_dir_from_acf,
    parse_library_folders_vdf,
};
#[cfg(windows)]
pub use game::{
    discover_windows_installation, discover_windows_locallow_root, discover_windows_steam_root,
};
pub use install::{
    ApplyPhase, ApplyProgress, InstallError, InstallRoots, InstallSummary, OwnershipManifest,
    RemoveReport, install_patch, install_patch_with_progress, remove_patch,
};
pub use network::{NetworkError, ReleaseClient, StageProgress};
pub use protocol::{
    InstallTarget, ManifestFile, PatchManifest, ProtocolError, ReleaseIndex, ReleaseIndexEntry,
};
pub use service::{
    DEFAULT_ROUTE, InstallOutcome, InstallProgress, InstalledPatchInfo, PatchFileInfo,
    PatcherPaths, RELEASE_CHANNEL, ServiceError, install_latest_compatible,
    install_latest_compatible_with_progress, install_roots, installed_patch_info, load_ownership,
    remove_installed_patch,
};
pub use settings::{AppSettings, SettingsError};
pub use uri::{URI_SCHEME, UriAction, UriError};
