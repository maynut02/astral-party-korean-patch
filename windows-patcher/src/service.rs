use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::game::{GameInstallation, GameRoute};
use crate::install::{
    ApplyPhase, ApplyProgress, InstallError, InstallRoots, InstallSummary, OwnershipManifest,
    RemoveIssueSummary, RemoveReport, install_patch_with_progress, installed_patch_change_count,
    remove_patch,
};
use crate::logging;
use crate::network::{NetworkError, ReleaseClient, StageProgress};
use crate::protocol::PatchManifest;

pub const RELEASE_CHANNEL: &str = "release";

#[derive(Debug, Error)]
pub enum ServiceError {
    #[error(transparent)]
    Game(#[from] crate::game::GameDetectError),
    #[error(transparent)]
    Network(#[from] NetworkError),
    #[error(transparent)]
    Install(#[from] InstallError),
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("failed to parse ownership manifest: {0}")]
    OwnershipJson(#[from] serde_json::Error),
    #[error("existing patch state changed externally: {0} file(s)")]
    ExistingPatchChanged(usize),
    #[error("existing patch cannot be safely removed: {0}")]
    ExistingPatchUnsafe(RemoveIssueSummary),
    #[error("legacy patch state migration conflict: {legacy_path} -> {destination}")]
    StateMigrationConflict {
        legacy_path: PathBuf,
        destination: PathBuf,
    },
    #[error("manifest is not compatible with the detected game")]
    IncompatibleManifest,
}

#[derive(Debug, Clone)]
pub struct PatcherPaths {
    pub state_root: PathBuf,
    pub routes_root: PathBuf,
    pub logs_root: PathBuf,
    pub settings_path: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RouteStatePaths {
    pub root: PathBuf,
    pub staging_root: PathBuf,
    pub backup_root: PathBuf,
    pub ownership_path: PathBuf,
    pub manifest_path: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StateMigrationItem {
    pub source: PathBuf,
    pub destination: PathBuf,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct StateMigrationReport {
    pub moved: Vec<StateMigrationItem>,
}

impl PatcherPaths {
    pub fn below(state_root: PathBuf) -> Self {
        Self {
            routes_root: state_root.join("routes"),
            logs_root: state_root.join("logs"),
            settings_path: state_root.join("settings.json"),
            state_root,
        }
    }

    #[cfg(windows)]
    pub fn windows_default() -> Result<Self, ServiceError> {
        let local_app_data = std::env::var_os("LOCALAPPDATA")
            .map(PathBuf::from)
            .ok_or_else(|| {
                std::io::Error::new(std::io::ErrorKind::NotFound, "LOCALAPPDATA is not set")
            })?;
        Ok(Self::below(local_app_data.join("AstralAutoPatcher")))
    }

    pub fn route_state(&self, route: GameRoute) -> RouteStatePaths {
        self.route_state_slug(route.slug())
    }

    fn route_state_slug(&self, slug: &str) -> RouteStatePaths {
        let root = self.routes_root.join(slug);
        RouteStatePaths {
            staging_root: root.join("staging"),
            backup_root: root.join("backup"),
            ownership_path: root.join("installed.json"),
            manifest_path: root.join("installed-manifest.json"),
            root,
        }
    }
}

impl RouteStatePaths {
    pub fn reset_staging(&self) -> Result<(), std::io::Error> {
        if self.staging_root.exists() {
            fs::remove_dir_all(&self.staging_root)?;
        }
        fs::create_dir_all(&self.staging_root)
    }
}

pub fn migrate_legacy_state(paths: &PatcherPaths) -> Result<StateMigrationReport, ServiceError> {
    let int_state = paths.route_state(GameRoute::IntSteam);
    let cn_state = paths.route_state(GameRoute::CnSteam);

    // CN legacy backup/staging lived below the INT legacy directories. Move those children first
    // so the remaining parent directories contain only INT state when they are migrated.
    // Steam state is persistent ownership data, so it is never overwritten automatically.
    let steam_moves = [
        (
            paths.state_root.join("installed-cn-steam.json"),
            cn_state.ownership_path.clone(),
        ),
        (
            paths.state_root.join("backup").join("cn-steam"),
            cn_state.backup_root.clone(),
        ),
        (
            paths.state_root.join("staging").join("cn-steam"),
            cn_state.staging_root.clone(),
        ),
        (
            paths.state_root.join("installed.json"),
            int_state.ownership_path.clone(),
        ),
        (
            paths.state_root.join("backup"),
            int_state.backup_root.clone(),
        ),
        (
            paths.state_root.join("staging"),
            int_state.staging_root.clone(),
        ),
    ];

    for (source, destination) in &steam_moves {
        if source.exists() && destination.exists() {
            return Err(ServiceError::StateMigrationConflict {
                legacy_path: source.clone(),
                destination: destination.clone(),
            });
        }
    }

    let mut report = StateMigrationReport::default();
    for (source, destination) in steam_moves {
        move_legacy_path(&source, &destination, &mut report)?;
    }

    Ok(report)
}

fn move_legacy_path(
    source: &Path,
    destination: &Path,
    report: &mut StateMigrationReport,
) -> Result<(), ServiceError> {
    if !source.exists() {
        return Ok(());
    }
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::rename(source, destination)?;
    report.moved.push(StateMigrationItem {
        source: source.to_owned(),
        destination: destination.to_owned(),
    });
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstalledPatchInfo {
    pub patch_version: String,
    pub catalog_hash: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PatchStateResetReport {
    pub ownership_removed: bool,
    pub backup_removed: bool,
    pub staging_removed: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InstallOutcome {
    AlreadyInstalled(InstalledPatchInfo),
    Installed(InstallSummary),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PatchFileInfo {
    pub download_name: String,
    pub install_path: String,
    pub download_size: u64,
    pub install_size: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum InstallProgress {
    Resolving,
    Selected {
        patch_version: String,
        files: Vec<PatchFileInfo>,
        download_total: u64,
        install_total: u64,
    },
    Downloading {
        file_index: usize,
        file_count: usize,
        file_name: String,
        current: u64,
        total: u64,
    },
    Extracting {
        file_index: usize,
        file_count: usize,
        file_name: String,
        current: u64,
        total: u64,
    },
    RemovingExisting {
        patch_version: String,
    },
    Applying {
        file_index: usize,
        file_count: usize,
        path: String,
        phase: ApplyPhase,
        current: u64,
        total: u64,
    },
}

pub fn install_roots(game: &GameInstallation) -> InstallRoots {
    InstallRoots {
        addressables: game.addressables_root.join("AssetBundles"),
        game_data: game.game_data_root.clone(),
    }
}

pub fn load_ownership(path: &Path) -> Result<Option<OwnershipManifest>, ServiceError> {
    if !path.is_file() {
        return Ok(None);
    }
    let raw = fs::read(path)?;
    Ok(Some(serde_json::from_slice(&raw)?))
}

pub fn installed_patch_info(path: &Path) -> Result<Option<InstalledPatchInfo>, ServiceError> {
    Ok(load_ownership(path)?.map(|ownership| InstalledPatchInfo {
        patch_version: ownership.patch_version,
        catalog_hash: ownership.catalog_hash,
    }))
}

pub fn reset_patch_state(
    paths: &PatcherPaths,
    route: GameRoute,
) -> Result<PatchStateResetReport, ServiceError> {
    let state = paths.route_state(route);
    logging::warn(format!(
        "resetting patch state: route={} root={}",
        route.as_str(),
        state.root.display()
    ));
    let ownership_removed = state.ownership_path.exists();
    let backup_removed = state.backup_root.exists();
    let staging_removed = state.staging_root.exists();

    if staging_removed {
        fs::remove_dir_all(&state.staging_root)?;
    }
    if backup_removed {
        fs::remove_dir_all(&state.backup_root)?;
    }
    if ownership_removed {
        fs::remove_file(&state.ownership_path)?;
    }
    if state.manifest_path.exists() {
        fs::remove_file(&state.manifest_path)?;
    }

    Ok(PatchStateResetReport {
        ownership_removed,
        backup_removed,
        staging_removed,
    })
}

pub fn remove_installed_patch(
    paths: &PatcherPaths,
    roots: &InstallRoots,
    route: GameRoute,
) -> Result<Option<RemoveReport>, ServiceError> {
    let state = paths.route_state(route);
    let Some(ownership) = load_ownership(&state.ownership_path)? else {
        logging::info(format!(
            "Steam remove: route={} no installed state",
            route.as_str()
        ));
        return Ok(None);
    };
    logging::info(format!(
        "Steam remove preflight: route={} patch={} created={} modified={} backup={}",
        route.as_str(),
        ownership.patch_version,
        ownership.created_files.len(),
        ownership.modified_files.len(),
        state.backup_root.display()
    ));
    repair_restore_backups(&ownership, &state)?;
    let report = remove_patch(&ownership, roots, &state.backup_root)?;
    let issues = report.issue_summary();
    if issues.total() > 0 {
        for issue in &report.issues {
            logging::warn(format!(
                "Steam remove issue: route={} target={:?} path={} kind={:?}",
                route.as_str(),
                issue.target,
                issue.path,
                issue.kind
            ));
        }
        logging::warn(format!(
            "Steam remove blocked: route={} summary={issues}",
            route.as_str()
        ));
        return Err(ServiceError::ExistingPatchUnsafe(issues));
    }
    if state.ownership_path.exists() {
        fs::remove_file(&state.ownership_path)?;
    }
    if state.backup_root.exists() {
        fs::remove_dir_all(&state.backup_root)?;
    }
    if state.manifest_path.exists() {
        fs::remove_file(&state.manifest_path)?;
    }
    logging::info(format!(
        "Steam remove complete: route={} removed={} restored={}",
        route.as_str(),
        report.removed,
        report.restored
    ));
    Ok(Some(report))
}

fn repair_restore_backups(
    ownership: &OwnershipManifest,
    state: &RouteStatePaths,
) -> Result<(), ServiceError> {
    let mut needs_restore = Vec::new();
    for modified in &ownership.modified_files {
        let backup = state.backup_root.join(&modified.backup_path);
        if !backup.is_file() || crate::install::sha256_file(&backup)? != modified.original_sha256 {
            needs_restore.push(modified);
        }
    }
    if needs_restore.is_empty() || !state.manifest_path.is_file() {
        return Ok(());
    }
    let raw = fs::read(&state.manifest_path)?;
    let manifest: PatchManifest = serde_json::from_slice(&raw)?;
    manifest.validate().map_err(InstallError::from)?;
    if manifest.patch.version != ownership.patch_version
        || manifest.game.catalog_hash != ownership.catalog_hash
    {
        return Err(ServiceError::IncompatibleManifest);
    }
    let user_agent = format!("AstralAutoPatcher/{}", env!("CARGO_PKG_VERSION"));
    let client = ReleaseClient::new(&user_agent)?;
    for modified in needs_restore {
        let Some(file) = manifest
            .files
            .iter()
            .find(|file| file.target == modified.target && file.path == modified.path)
        else {
            continue;
        };
        if file.source_sha256.as_deref() != Some(modified.original_sha256.as_str()) {
            continue;
        }
        let backup = state.backup_root.join(&modified.backup_path);
        logging::info(format!(
            "Steam restore source download: path={}",
            modified.path
        ));
        client.download_original_file(file, &backup)?;
    }
    Ok(())
}

fn prepare_release_restore_backups(
    client: &ReleaseClient,
    manifest: &PatchManifest,
    roots: &InstallRoots,
    state: &RouteStatePaths,
) -> Result<(), ServiceError> {
    for file in &manifest.files {
        let (Some(source_sha256), Some(source_size)) = (&file.source_sha256, file.source_size)
        else {
            continue;
        };
        let destination = match file.target {
            crate::protocol::InstallTarget::Addressables => roots.addressables.join(&file.path),
            crate::protocol::InstallTarget::GameData => roots.game_data.join(&file.path),
        };
        if !destination.is_file() {
            continue;
        }

        let actual_size = destination.metadata()?.len();
        if actual_size != source_size {
            return Err(InstallError::SizeMismatch(destination, source_size, actual_size).into());
        }
        let actual_sha256 = crate::install::sha256_file(&destination)?;
        if actual_sha256 != *source_sha256 {
            return Err(InstallError::HashMismatch(
                destination,
                source_sha256.clone(),
                actual_sha256,
            )
            .into());
        }

        let backup = state
            .backup_root
            .join(file.target.staging_dir())
            .join(&file.path);
        if backup.is_file()
            && backup.metadata()?.len() == source_size
            && crate::install::sha256_file(&backup)? == *source_sha256
        {
            continue;
        }
        logging::info(format!(
            "Steam restore source prepare: path={} source={}",
            file.path,
            file.source_download_url.as_deref().unwrap_or("missing")
        ));
        client.download_original_file(file, &backup)?;
    }
    Ok(())
}

fn ensure_manifest_compatible(
    manifest: &PatchManifest,
    game: &GameInstallation,
    route: &str,
) -> Result<(), ServiceError> {
    if manifest.patch.route != route
        || manifest.game.version != game.catalog.version
        || manifest.game.catalog_hash != game.catalog.hash
    {
        return Err(ServiceError::IncompatibleManifest);
    }
    Ok(())
}

pub fn install_latest_compatible(
    release_index_url: &str,
    paths: &PatcherPaths,
    game: &GameInstallation,
) -> Result<InstallOutcome, ServiceError> {
    install_latest_compatible_with_progress(release_index_url, paths, game, |_| {})
}

pub fn install_latest_compatible_with_progress<F>(
    release_index_url: &str,
    paths: &PatcherPaths,
    game: &GameInstallation,
    mut progress: F,
) -> Result<InstallOutcome, ServiceError>
where
    F: FnMut(InstallProgress),
{
    let roots = install_roots(game);
    let state = paths.route_state(game.route);
    let route = game.route.as_str();
    logging::info(format!(
        "Steam install resolve: route={} game_version={} catalog={} state={}",
        route,
        game.catalog.version,
        game.catalog.hash,
        state.root.display()
    ));
    let user_agent = format!("AstralAutoPatcher/{}", env!("CARGO_PKG_VERSION"));
    let client = ReleaseClient::new(&user_agent)?;
    progress(InstallProgress::Resolving);
    let index = client.fetch_release_index(release_index_url)?;
    let (_, manifest) = client.fetch_compatible_manifest(
        &index,
        route,
        &game.catalog.version,
        &game.catalog.hash,
        RELEASE_CHANNEL,
    )?;
    ensure_manifest_compatible(&manifest, game, route)?;
    logging::info(format!(
        "Steam manifest selected: route={} patch={} files={}",
        route,
        manifest.patch.version,
        manifest.files.len()
    ));

    let files = manifest
        .files
        .iter()
        .map(|file| PatchFileInfo {
            download_name: download_name(&file.download_url),
            install_path: file.path.clone(),
            download_size: file.download_size,
            install_size: file.size,
        })
        .collect::<Vec<_>>();
    let download_total = manifest.files.iter().map(|file| file.download_size).sum();
    let install_total = manifest.files.iter().map(|file| file.size).sum();
    progress(InstallProgress::Selected {
        patch_version: manifest.patch.version.clone(),
        files,
        download_total,
        install_total,
    });

    if let Some(existing) = load_ownership(&state.ownership_path)? {
        if existing.patch_version == manifest.patch.version
            && existing.catalog_hash == manifest.game.catalog_hash
        {
            let changed_files = installed_patch_change_count(&existing, &roots)?;
            if changed_files > 0 {
                logging::warn(format!(
                    "Steam installed state differs from files: route={} changed={changed_files}",
                    route
                ));
                return Err(ServiceError::ExistingPatchChanged(changed_files));
            }
            return Ok(InstallOutcome::AlreadyInstalled(InstalledPatchInfo {
                patch_version: existing.patch_version,
                catalog_hash: existing.catalog_hash,
            }));
        }
        progress(InstallProgress::RemovingExisting {
            patch_version: existing.patch_version.clone(),
        });
        remove_installed_patch(paths, &roots, game.route)?;
    }

    state.reset_staging()?;
    prepare_release_restore_backups(&client, &manifest, &roots, &state)?;
    client.stage_manifest_files_with_progress(
        &manifest,
        &state.staging_root,
        |event| match event {
            StageProgress::Downloading {
                file_index,
                file_count,
                file_name,
                current,
                total,
            } => progress(InstallProgress::Downloading {
                file_index,
                file_count,
                file_name,
                current,
                total,
            }),
            StageProgress::Extracting {
                file_index,
                file_count,
                file_name,
                current,
                total,
            } => progress(InstallProgress::Extracting {
                file_index,
                file_count,
                file_name,
                current,
                total,
            }),
        },
    )?;
    let summary = install_patch_with_progress(
        &manifest,
        &state.staging_root,
        &roots,
        &state.backup_root,
        &state.ownership_path,
        |ApplyProgress {
             file_index,
             file_count,
             path,
             phase,
             current,
             total,
         }| {
            progress(InstallProgress::Applying {
                file_index,
                file_count,
                path,
                phase,
                current,
                total,
            });
        },
    )?;
    let manifest_json = serde_json::to_vec_pretty(&manifest)?;
    let manifest_temp = state.manifest_path.with_extension("json.tmp");
    if let Some(parent) = state.manifest_path.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(&manifest_temp, manifest_json)?;
    if state.manifest_path.exists() {
        fs::remove_file(&state.manifest_path)?;
    }
    fs::rename(manifest_temp, &state.manifest_path)?;
    let _ = fs::remove_dir_all(&state.staging_root);
    logging::info(format!(
        "Steam install complete: route={} patch={} created={} modified={}",
        route, manifest.patch.version, summary.created, summary.modified
    ));
    Ok(InstallOutcome::Installed(summary))
}

fn download_name(url: &str) -> String {
    url.rsplit('/')
        .next()
        .filter(|value| !value.is_empty())
        .unwrap_or(url)
        .to_owned()
}

#[cfg(test)]
mod tests {
    use sha2::{Digest, Sha256};
    use tempfile::tempdir;

    use super::*;
    use crate::install::install_patch;
    use crate::protocol::{InstallTarget, ManifestFile, PatchManifest, PatchMetadata, TargetGame};

    #[test]
    fn download_name_extracts_release_asset_name() {
        assert_eq!(
            download_name("https://example.test/releases/v1/assets/game-data-data.unity3d.gz"),
            "game-data-data.unity3d.gz"
        );
    }

    fn manifest(version: &str, hash: &str) -> PatchManifest {
        PatchManifest {
            schema_version: 2,
            patch: PatchMetadata {
                version: version.into(),
                channel: "release".into(),
                route: GameRoute::IntSteam.as_str().into(),
                build_id: "build".into(),
                translation_fingerprint: "a".repeat(64),
            },
            game: TargetGame {
                version: "3.2.0".into(),
                revision: "1042".into(),
                catalog_hash: "b".repeat(32),
            },
            files: vec![ManifestFile {
                target: InstallTarget::GameData,
                path: "data.unity3d".into(),
                operation: "replace".into(),
                download_url: "https://example.test/data.gz".into(),
                download_sha256: "d".repeat(64),
                download_size: 5,
                compression: "gzip".into(),
                sha256: hash.into(),
                size: 7,
                source_download_url: None,
                source_download_sha256: None,
                source_download_size: None,
                source_sha256: None,
                source_size: None,
            }],
        }
    }

    #[test]
    fn upgrade_removes_old_patch_before_new_install() {
        let temp = tempdir().unwrap();
        let paths = PatcherPaths::below(temp.path().join("state"));
        let roots = InstallRoots {
            addressables: temp.path().join("addressables"),
            game_data: temp.path().join("game-data"),
        };
        fs::create_dir_all(&roots.game_data).unwrap();
        fs::write(roots.game_data.join("data.unity3d"), b"original").unwrap();

        let payload = b"patch01";
        let hash = format!("{:x}", Sha256::digest(payload));
        let first = manifest("v1", &hash);
        let state = paths.route_state(GameRoute::IntSteam);
        let stage = state.staging_root.join("game-data/data.unity3d");
        fs::create_dir_all(stage.parent().unwrap()).unwrap();
        fs::write(&stage, payload).unwrap();
        install_patch(
            &first,
            &state.staging_root,
            &roots,
            &state.backup_root,
            &state.ownership_path,
        )
        .unwrap();

        let report = remove_installed_patch(&paths, &roots, GameRoute::IntSteam)
            .unwrap()
            .unwrap();
        assert_eq!(report.restored, 1);
        assert_eq!(
            fs::read(roots.game_data.join("data.unity3d")).unwrap(),
            b"original"
        );
        assert!(!state.ownership_path.exists());
    }

    #[test]
    fn route_state_is_fully_separated() {
        let temp = tempdir().unwrap();
        let paths = PatcherPaths::below(temp.path().join("state"));
        let int = paths.route_state(GameRoute::IntSteam);
        let cn = paths.route_state(GameRoute::CnSteam);
        assert_eq!(int.root, paths.routes_root.join("int-steam"));
        assert_eq!(int.ownership_path, int.root.join("installed.json"));
        assert_eq!(int.backup_root, int.root.join("backup"));
        assert_eq!(int.staging_root, int.root.join("staging"));
        assert_eq!(cn.root, paths.routes_root.join("cn-steam"));
        assert_eq!(cn.ownership_path, cn.root.join("installed.json"));
        assert_eq!(cn.backup_root, cn.root.join("backup"));
        assert_eq!(cn.staging_root, cn.root.join("staging"));
        assert!(!int.root.starts_with(&cn.root));
        assert!(!cn.root.starts_with(&int.root));
    }

    #[test]
    fn migrates_legacy_route_state_without_cross_contamination() {
        let temp = tempdir().unwrap();
        let paths = PatcherPaths::below(temp.path().join("state"));
        fs::create_dir_all(paths.state_root.join("backup/cn-steam")).unwrap();
        fs::create_dir_all(paths.state_root.join("staging/cn-steam")).unwrap();
        fs::write(paths.state_root.join("installed.json"), b"int").unwrap();
        fs::write(paths.state_root.join("installed-cn-steam.json"), b"cn").unwrap();
        fs::write(paths.state_root.join("backup/int.dat"), b"int-backup").unwrap();
        fs::write(
            paths.state_root.join("backup/cn-steam/cn.dat"),
            b"cn-backup",
        )
        .unwrap();
        fs::write(paths.state_root.join("staging/int.dat"), b"int-stage").unwrap();
        fs::write(
            paths.state_root.join("staging/cn-steam/cn.dat"),
            b"cn-stage",
        )
        .unwrap();

        let report = migrate_legacy_state(&paths).unwrap();
        assert_eq!(report.moved.len(), 6);
        let int = paths.route_state(GameRoute::IntSteam);
        let cn = paths.route_state(GameRoute::CnSteam);
        assert_eq!(fs::read(&int.ownership_path).unwrap(), b"int");
        assert_eq!(
            fs::read(int.backup_root.join("int.dat")).unwrap(),
            b"int-backup"
        );
        assert!(!int.backup_root.join("cn-steam").exists());
        assert_eq!(fs::read(&cn.ownership_path).unwrap(), b"cn");
        assert_eq!(
            fs::read(cn.backup_root.join("cn.dat")).unwrap(),
            b"cn-backup"
        );
        assert_eq!(
            fs::read(cn.staging_root.join("cn.dat")).unwrap(),
            b"cn-stage"
        );
    }

    #[test]
    fn legacy_state_migration_refuses_to_overwrite_new_state() {
        let temp = tempdir().unwrap();
        let paths = PatcherPaths::below(temp.path().join("state"));
        fs::create_dir_all(&paths.state_root).unwrap();
        fs::write(paths.state_root.join("installed.json"), b"legacy").unwrap();
        let int = paths.route_state(GameRoute::IntSteam);
        fs::create_dir_all(int.ownership_path.parent().unwrap()).unwrap();
        fs::write(&int.ownership_path, b"new").unwrap();

        let error = migrate_legacy_state(&paths).unwrap_err();
        assert!(matches!(error, ServiceError::StateMigrationConflict { .. }));
        assert_eq!(fs::read(&int.ownership_path).unwrap(), b"new");
        assert_eq!(
            fs::read(paths.state_root.join("installed.json")).unwrap(),
            b"legacy"
        );
    }

    #[test]
    fn restored_game_file_is_detected_as_changed_patch_state() {
        let temp = tempdir().unwrap();
        let paths = PatcherPaths::below(temp.path().join("state"));
        let roots = InstallRoots {
            addressables: temp.path().join("addressables"),
            game_data: temp.path().join("game-data"),
        };
        fs::create_dir_all(&roots.game_data).unwrap();
        fs::write(roots.game_data.join("data.unity3d"), b"original").unwrap();

        let payload = b"patch01";
        let hash = format!("{:x}", Sha256::digest(payload));
        let current = manifest("v1", &hash);
        let state = paths.route_state(GameRoute::IntSteam);
        let stage = state.staging_root.join("game-data/data.unity3d");
        fs::create_dir_all(stage.parent().unwrap()).unwrap();
        fs::write(&stage, payload).unwrap();
        install_patch(
            &current,
            &state.staging_root,
            &roots,
            &state.backup_root,
            &state.ownership_path,
        )
        .unwrap();

        fs::write(roots.game_data.join("data.unity3d"), b"original").unwrap();
        let ownership = load_ownership(&state.ownership_path).unwrap().unwrap();
        let changed = installed_patch_change_count(&ownership, &roots).unwrap();
        assert_eq!(changed, 1);
    }

    #[test]
    fn reset_patch_state_removes_only_patcher_metadata() {
        let temp = tempdir().unwrap();
        let paths = PatcherPaths::below(temp.path().join("state"));
        let state = paths.route_state(GameRoute::IntSteam);
        let game_file = temp.path().join("game/data.unity3d");
        fs::create_dir_all(game_file.parent().unwrap()).unwrap();
        fs::write(&game_file, b"steam-restored-game-data").unwrap();
        fs::create_dir_all(&state.backup_root).unwrap();
        fs::create_dir_all(&state.staging_root).unwrap();
        fs::create_dir_all(state.ownership_path.parent().unwrap()).unwrap();
        fs::write(&state.ownership_path, b"stale ownership").unwrap();
        fs::write(state.backup_root.join("backup.dat"), b"backup").unwrap();
        fs::write(state.staging_root.join("stage.dat"), b"stage").unwrap();

        let report = reset_patch_state(&paths, GameRoute::IntSteam).unwrap();

        assert_eq!(
            report,
            PatchStateResetReport {
                ownership_removed: true,
                backup_removed: true,
                staging_removed: true,
            }
        );
        assert!(!state.ownership_path.exists());
        assert!(!state.backup_root.exists());
        assert!(!state.staging_root.exists());
        assert_eq!(fs::read(&game_file).unwrap(), b"steam-restored-game-data");
    }

    #[test]
    fn external_changes_block_automatic_upgrade() {
        let temp = tempdir().unwrap();
        let paths = PatcherPaths::below(temp.path().join("state"));
        let roots = InstallRoots {
            addressables: temp.path().join("addressables"),
            game_data: temp.path().join("game-data"),
        };
        fs::create_dir_all(&roots.game_data).unwrap();
        fs::write(roots.game_data.join("data.unity3d"), b"original").unwrap();

        let payload = b"patch01";
        let hash = format!("{:x}", Sha256::digest(payload));
        let first = manifest("v1", &hash);
        let state = paths.route_state(GameRoute::IntSteam);
        let stage = state.staging_root.join("game-data/data.unity3d");
        fs::create_dir_all(stage.parent().unwrap()).unwrap();
        fs::write(&stage, payload).unwrap();
        install_patch(
            &first,
            &state.staging_root,
            &roots,
            &state.backup_root,
            &state.ownership_path,
        )
        .unwrap();
        fs::write(roots.game_data.join("data.unity3d"), b"changed").unwrap();

        let err = remove_installed_patch(&paths, &roots, GameRoute::IntSteam).unwrap_err();
        assert!(matches!(
            err,
            ServiceError::ExistingPatchUnsafe(RemoveIssueSummary {
                modified_externally: 1,
                ..
            })
        ));
        assert!(state.ownership_path.exists());
    }
}
