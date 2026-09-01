pub mod cli;
pub mod game;
pub mod install;
pub mod logging;
pub mod network;
pub mod protocol;
pub mod registration;
pub mod service;
pub mod settings;
pub mod tui;
pub mod updater;
pub mod uri;

pub use game::{
    ADDRESSABLES_DIR, CatalogIdentity, GameDetectError, GameInstallation, GameRoute, STEAM_APP_ID,
    build_installation, discover_latest_catalog, find_install_from_libraries,
    normalize_locallow_root, normalize_steam_root, parse_install_dir_from_acf,
    parse_library_folders_vdf,
};
#[cfg(windows)]
pub use game::{
    detect_windows_routes, discover_windows_installation, discover_windows_locallow_root,
    discover_windows_steam_root,
};
pub use install::{
    ApplyPhase, ApplyProgress, InstallError, InstallRoots, InstallSummary, OwnershipManifest,
    RemoveIssue, RemoveIssueKind, RemoveIssueSummary, RemoveReport, install_patch,
    install_patch_with_progress, installed_patch_change_count, remove_patch,
};
pub use network::{NetworkError, ReleaseClient, StageProgress};
pub use protocol::{
    InstallTarget, ManifestFile, PatchManifest, ProtocolError, ReleaseIndex, ReleaseIndexEntry,
};
pub use service::{
    InstallOutcome, InstallProgress, InstalledPatchInfo, PatchFileInfo, PatchStateResetReport,
    PatcherPaths, RELEASE_CHANNEL, RouteStatePaths, ServiceError, StateMigrationItem,
    StateMigrationReport, install_latest_compatible, install_latest_compatible_with_progress,
    install_roots, installed_patch_info, load_ownership, migrate_legacy_state,
    remove_installed_patch, reset_patch_state,
};
pub use settings::{AppSettings, SettingsError};
pub use uri::{URI_SCHEME, UriAction, UriError, UriRequest};
