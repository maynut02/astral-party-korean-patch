use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use thiserror::Error;

use crate::game::GameInstallation;
use crate::install::{
    ApplyPhase, ApplyProgress, InstallError, InstallRoots, InstallSummary, OwnershipManifest,
    RemoveReport, install_patch_with_progress, remove_patch,
};
use crate::network::{NetworkError, ReleaseClient, StageProgress};
use crate::protocol::PatchManifest;

pub const DEFAULT_ROUTE: &str = "INT_STEAM";

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
    #[error("existing patch cannot be safely removed because {0} files changed externally")]
    ExistingPatchChanged(usize),
    #[error("manifest is not compatible with the detected game")]
    IncompatibleManifest,
}

#[derive(Debug, Clone)]
pub struct PatcherPaths {
    pub state_root: PathBuf,
    pub staging_root: PathBuf,
    pub backup_root: PathBuf,
    pub ownership_path: PathBuf,
    pub settings_path: PathBuf,
}

impl PatcherPaths {
    pub fn below(state_root: PathBuf) -> Self {
        Self {
            staging_root: state_root.join("staging"),
            backup_root: state_root.join("backup"),
            ownership_path: state_root.join("installed.json"),
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

    pub fn reset_staging(&self) -> Result<(), std::io::Error> {
        if self.staging_root.exists() {
            fs::remove_dir_all(&self.staging_root)?;
        }
        fs::create_dir_all(&self.staging_root)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstalledPatchInfo {
    pub patch_version: String,
    pub catalog_hash: String,
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

pub fn remove_installed_patch(
    paths: &PatcherPaths,
    roots: &InstallRoots,
) -> Result<Option<RemoveReport>, ServiceError> {
    let Some(ownership) = load_ownership(&paths.ownership_path)? else {
        return Ok(None);
    };
    let report = remove_patch(&ownership, roots, &paths.backup_root)?;
    if report.skipped > 0 {
        return Err(ServiceError::ExistingPatchChanged(report.skipped));
    }
    if paths.ownership_path.exists() {
        fs::remove_file(&paths.ownership_path)?;
    }
    if paths.backup_root.exists() {
        fs::remove_dir_all(&paths.backup_root)?;
    }
    Ok(Some(report))
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
    channel: &str,
    paths: &PatcherPaths,
    game: &GameInstallation,
) -> Result<InstallOutcome, ServiceError> {
    install_latest_compatible_with_progress(release_index_url, channel, paths, game, |_| {})
}

pub fn install_latest_compatible_with_progress<F>(
    release_index_url: &str,
    channel: &str,
    paths: &PatcherPaths,
    game: &GameInstallation,
    mut progress: F,
) -> Result<InstallOutcome, ServiceError>
where
    F: FnMut(InstallProgress),
{
    let roots = install_roots(game);
    let user_agent = format!("AstralAutoPatcher/{}", env!("CARGO_PKG_VERSION"));
    let client = ReleaseClient::new(&user_agent)?;
    progress(InstallProgress::Resolving);
    let index = client.fetch_release_index(release_index_url)?;
    let (_, manifest) = client.fetch_compatible_manifest(
        &index,
        DEFAULT_ROUTE,
        &game.catalog.version,
        &game.catalog.hash,
        channel,
    )?;
    ensure_manifest_compatible(&manifest, game, DEFAULT_ROUTE)?;

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

    if let Some(existing) = load_ownership(&paths.ownership_path)? {
        if existing.patch_version == manifest.patch.version
            && existing.catalog_hash == manifest.game.catalog_hash
        {
            return Ok(InstallOutcome::AlreadyInstalled(InstalledPatchInfo {
                patch_version: existing.patch_version,
                catalog_hash: existing.catalog_hash,
            }));
        }
        progress(InstallProgress::RemovingExisting {
            patch_version: existing.patch_version.clone(),
        });
        remove_installed_patch(paths, &roots)?;
    }

    paths.reset_staging()?;
    client.stage_manifest_files_with_progress(
        &manifest,
        &paths.staging_root,
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
        &paths.staging_root,
        &roots,
        &paths.backup_root,
        &paths.ownership_path,
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
    let _ = fs::remove_dir_all(&paths.staging_root);
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
                channel: "preview".into(),
                route: DEFAULT_ROUTE.into(),
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
        let stage = paths.staging_root.join("game-data/data.unity3d");
        fs::create_dir_all(stage.parent().unwrap()).unwrap();
        fs::write(&stage, payload).unwrap();
        install_patch(
            &first,
            &paths.staging_root,
            &roots,
            &paths.backup_root,
            &paths.ownership_path,
        )
        .unwrap();

        let report = remove_installed_patch(&paths, &roots).unwrap().unwrap();
        assert_eq!(report.restored, 1);
        assert_eq!(
            fs::read(roots.game_data.join("data.unity3d")).unwrap(),
            b"original"
        );
        assert!(!paths.ownership_path.exists());
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
        let stage = paths.staging_root.join("game-data/data.unity3d");
        fs::create_dir_all(stage.parent().unwrap()).unwrap();
        fs::write(&stage, payload).unwrap();
        install_patch(
            &first,
            &paths.staging_root,
            &roots,
            &paths.backup_root,
            &paths.ownership_path,
        )
        .unwrap();
        fs::write(roots.game_data.join("data.unity3d"), b"changed").unwrap();

        let err = remove_installed_patch(&paths, &roots).unwrap_err();
        assert!(matches!(err, ServiceError::ExistingPatchChanged(1)));
        assert!(paths.ownership_path.exists());
    }
}
