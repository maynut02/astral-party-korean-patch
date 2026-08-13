use std::fs;
use std::io::{self, Read};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;

use crate::protocol::{InstallTarget, PatchManifest, ProtocolError, validate_relative_path};

#[derive(Debug, Error)]
pub enum InstallError {
    #[error(transparent)]
    Io(#[from] io::Error),
    #[error(transparent)]
    Protocol(#[from] ProtocolError),
    #[error("staged file missing: {0}")]
    MissingStage(PathBuf),
    #[error("file size mismatch for {0}: expected {1}, actual {2}")]
    SizeMismatch(PathBuf, u64, u64),
    #[error("sha256 mismatch for {0}: expected {1}, actual {2}")]
    HashMismatch(PathBuf, String, String),
    #[error("ownership manifest is incompatible with current patch")]
    OwnershipMismatch,
    #[error("failed to serialize ownership manifest: {0}")]
    Json(#[from] serde_json::Error),
}

#[derive(Debug, Clone)]
pub struct InstallRoots {
    pub addressables: PathBuf,
    pub game_data: PathBuf,
}

impl InstallRoots {
    fn root(&self, target: InstallTarget) -> &Path {
        match target {
            InstallTarget::Addressables => &self.addressables,
            InstallTarget::GameData => &self.game_data,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OwnedCreatedFile {
    pub target: InstallTarget,
    pub path: String,
    pub installed_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OwnedModifiedFile {
    pub target: InstallTarget,
    pub path: String,
    pub original_sha256: String,
    pub patched_sha256: String,
    pub backup_path: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OwnershipManifest {
    pub schema_version: u32,
    pub patch_version: String,
    pub catalog_hash: String,
    pub created_files: Vec<OwnedCreatedFile>,
    pub modified_files: Vec<OwnedModifiedFile>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InstallSummary {
    pub created: usize,
    pub modified: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RemoveReport {
    pub removed: usize,
    pub restored: usize,
    pub skipped: usize,
}

pub fn sha256_file(path: &Path) -> Result<String, io::Error> {
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 128 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn verify_file(path: &Path, expected_size: u64, expected_hash: &str) -> Result<(), InstallError> {
    if !path.is_file() {
        return Err(InstallError::MissingStage(path.to_owned()));
    }
    let actual_size = path.metadata()?.len();
    if actual_size != expected_size {
        return Err(InstallError::SizeMismatch(
            path.to_owned(),
            expected_size,
            actual_size,
        ));
    }
    let actual_hash = sha256_file(path)?;
    if actual_hash != expected_hash {
        return Err(InstallError::HashMismatch(
            path.to_owned(),
            expected_hash.to_owned(),
            actual_hash,
        ));
    }
    Ok(())
}

fn target_path(
    roots: &InstallRoots,
    target: InstallTarget,
    relative: &str,
) -> Result<PathBuf, InstallError> {
    validate_relative_path(relative)?;
    Ok(roots.root(target).join(relative))
}

fn staged_path(
    staging_root: &Path,
    target: InstallTarget,
    relative: &str,
) -> Result<PathBuf, InstallError> {
    validate_relative_path(relative)?;
    Ok(staging_root.join(target.staging_dir()).join(relative))
}

fn backup_path(
    backup_root: &Path,
    target: InstallTarget,
    relative: &str,
) -> Result<PathBuf, InstallError> {
    validate_relative_path(relative)?;
    Ok(backup_root.join(target.staging_dir()).join(relative))
}

fn copy_replace(source: &Path, destination: &Path) -> Result<(), io::Error> {
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)?;
    }
    let temp = destination.with_extension("astral-install-tmp");
    fs::copy(source, &temp)?;
    if destination.exists() {
        fs::remove_file(destination)?;
    }
    fs::rename(&temp, destination)?;
    Ok(())
}

fn rollback_install(roots: &InstallRoots, backup_root: &Path, ownership: &OwnershipManifest) {
    for created in ownership.created_files.iter().rev() {
        if let Ok(path) = target_path(roots, created.target, &created.path) {
            let _ = fs::remove_file(path);
        }
    }
    for modified in ownership.modified_files.iter().rev() {
        let destination = match target_path(roots, modified.target, &modified.path) {
            Ok(path) => path,
            Err(_) => continue,
        };
        let backup = backup_root.join(&modified.backup_path);
        let _ = copy_replace(&backup, &destination);
    }
}

pub fn install_patch(
    manifest: &PatchManifest,
    staging_root: &Path,
    roots: &InstallRoots,
    backup_root: &Path,
    ownership_path: &Path,
) -> Result<InstallSummary, InstallError> {
    manifest.validate()?;
    let mut ownership = OwnershipManifest {
        schema_version: 1,
        patch_version: manifest.patch.version.clone(),
        catalog_hash: manifest.game.catalog_hash.clone(),
        created_files: Vec::new(),
        modified_files: Vec::new(),
    };

    let result = (|| {
        for file in &manifest.files {
            let stage = staged_path(staging_root, file.target, &file.path)?;
            verify_file(&stage, file.size, &file.sha256)?;
            let destination = target_path(roots, file.target, &file.path)?;

            if destination.exists() {
                let original_hash = sha256_file(&destination)?;
                let backup = backup_path(backup_root, file.target, &file.path)?;
                if let Some(parent) = backup.parent() {
                    fs::create_dir_all(parent)?;
                }
                fs::copy(&destination, &backup)?;
                ownership.modified_files.push(OwnedModifiedFile {
                    target: file.target,
                    path: file.path.clone(),
                    original_sha256: original_hash,
                    patched_sha256: file.sha256.clone(),
                    backup_path: backup
                        .strip_prefix(backup_root)
                        .expect("backup path is below root")
                        .to_string_lossy()
                        .replace('\\', "/"),
                });
            } else {
                ownership.created_files.push(OwnedCreatedFile {
                    target: file.target,
                    path: file.path.clone(),
                    installed_sha256: file.sha256.clone(),
                });
            }

            copy_replace(&stage, &destination)?;
            verify_file(&destination, file.size, &file.sha256)?;
        }

        if let Some(parent) = ownership_path.parent() {
            fs::create_dir_all(parent)?;
        }
        let json = serde_json::to_vec_pretty(&ownership)?;
        let temp = ownership_path.with_extension("json.tmp");
        fs::write(&temp, json)?;
        if ownership_path.exists() {
            fs::remove_file(ownership_path)?;
        }
        fs::rename(temp, ownership_path)?;
        Ok::<(), InstallError>(())
    })();

    if let Err(error) = result {
        rollback_install(roots, backup_root, &ownership);
        return Err(error);
    }

    Ok(InstallSummary {
        created: ownership.created_files.len(),
        modified: ownership.modified_files.len(),
    })
}

pub fn remove_patch(
    ownership: &OwnershipManifest,
    roots: &InstallRoots,
    backup_root: &Path,
) -> Result<RemoveReport, InstallError> {
    if ownership.schema_version != 1 {
        return Err(InstallError::OwnershipMismatch);
    }

    // Preflight every owned path before mutating anything. If even one file changed outside the
    // patcher, leave all remaining patch files untouched so uninstall/upgrade does not become
    // partial.
    let mut skipped = 0;
    for created in &ownership.created_files {
        let path = target_path(roots, created.target, &created.path)?;
        if path.exists() && sha256_file(&path)? != created.installed_sha256 {
            skipped += 1;
        }
    }
    for modified in &ownership.modified_files {
        let destination = target_path(roots, modified.target, &modified.path)?;
        if !destination.exists() {
            skipped += 1;
            continue;
        }
        let current_hash = sha256_file(&destination)?;
        if current_hash == modified.original_sha256 {
            // Already restored, for example after an interrupted previous cleanup.
            continue;
        }
        if current_hash != modified.patched_sha256 {
            skipped += 1;
            continue;
        }
        validate_relative_path(&modified.backup_path)?;
        let backup = backup_root.join(&modified.backup_path);
        if !backup.is_file() || sha256_file(&backup)? != modified.original_sha256 {
            skipped += 1;
        }
    }
    if skipped > 0 {
        return Ok(RemoveReport {
            removed: 0,
            restored: 0,
            skipped,
        });
    }

    let mut report = RemoveReport {
        removed: 0,
        restored: 0,
        skipped: 0,
    };
    for created in &ownership.created_files {
        let path = target_path(roots, created.target, &created.path)?;
        if !path.exists() {
            continue;
        }
        if sha256_file(&path)? != created.installed_sha256 {
            return Err(InstallError::OwnershipMismatch);
        }
        fs::remove_file(path)?;
        report.removed += 1;
    }
    for modified in &ownership.modified_files {
        let destination = target_path(roots, modified.target, &modified.path)?;
        let current_hash = sha256_file(&destination)?;
        if current_hash == modified.original_sha256 {
            continue;
        }
        if current_hash != modified.patched_sha256 {
            return Err(InstallError::OwnershipMismatch);
        }
        validate_relative_path(&modified.backup_path)?;
        let backup = backup_root.join(&modified.backup_path);
        if !backup.is_file() || sha256_file(&backup)? != modified.original_sha256 {
            return Err(InstallError::OwnershipMismatch);
        }
        copy_replace(&backup, &destination)?;
        if sha256_file(&destination)? != modified.original_sha256 {
            return Err(InstallError::OwnershipMismatch);
        }
        report.restored += 1;
    }

    Ok(report)
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::*;
    use crate::protocol::{ManifestFile, PatchMetadata, TargetGame};

    fn manifest(file: ManifestFile) -> PatchManifest {
        PatchManifest {
            schema_version: 1,
            patch: PatchMetadata {
                version: "v1".into(),
                channel: "preview".into(),
                route: "INT_STEAM".into(),
                build_id: "build-1".into(),
                translation_fingerprint: "a".repeat(64),
            },
            game: TargetGame {
                version: "3.2.0".into(),
                revision: "1042".into(),
                catalog_hash: "b".repeat(32),
            },
            files: vec![file],
        }
    }

    fn roots(root: &Path) -> InstallRoots {
        InstallRoots {
            addressables: root.join("addressables-root"),
            game_data: root.join("game-data-root"),
        }
    }

    #[test]
    fn installs_created_file_and_removes_only_when_hash_matches() {
        let temp = tempdir().unwrap();
        let staging = temp.path().join("staging");
        let roots = roots(temp.path());
        let backup = temp.path().join("backup");
        let ownership_path = temp.path().join("installed.json");
        let payload = b"patched";
        let hash = format!("{:x}", Sha256::digest(payload));
        let stage = staging.join("addressables/root/hash/__data");
        fs::create_dir_all(stage.parent().unwrap()).unwrap();
        fs::write(&stage, payload).unwrap();
        let patch = manifest(ManifestFile {
            target: InstallTarget::Addressables,
            path: "root/hash/__data".into(),
            operation: "create".into(),
            download_url: "https://example.test/created".into(),
            sha256: hash,
            size: payload.len() as u64,
        });

        let summary = install_patch(&patch, &staging, &roots, &backup, &ownership_path).unwrap();
        assert_eq!(summary.created, 1);
        let installed = roots.addressables.join("root/hash/__data");
        assert_eq!(fs::read(&installed).unwrap(), payload);

        let ownership: OwnershipManifest =
            serde_json::from_slice(&fs::read(&ownership_path).unwrap()).unwrap();
        let report = remove_patch(&ownership, &roots, &backup).unwrap();
        assert_eq!(report.removed, 1);
        assert!(!installed.exists());
    }

    #[test]
    fn restores_modified_file_and_preserves_external_change() {
        let temp = tempdir().unwrap();
        let staging = temp.path().join("staging");
        let roots = roots(temp.path());
        fs::create_dir_all(&roots.game_data).unwrap();
        let destination = roots.game_data.join("data.unity3d");
        fs::write(&destination, b"original").unwrap();
        let backup = temp.path().join("backup");
        let ownership_path = temp.path().join("installed.json");
        let payload = b"patched";
        let hash = format!("{:x}", Sha256::digest(payload));
        let stage = staging.join("game-data/data.unity3d");
        fs::create_dir_all(stage.parent().unwrap()).unwrap();
        fs::write(&stage, payload).unwrap();
        let patch = manifest(ManifestFile {
            target: InstallTarget::GameData,
            path: "data.unity3d".into(),
            operation: "replace".into(),
            download_url: "https://example.test/replaced".into(),
            sha256: hash,
            size: payload.len() as u64,
        });

        install_patch(&patch, &staging, &roots, &backup, &ownership_path).unwrap();
        let ownership: OwnershipManifest =
            serde_json::from_slice(&fs::read(&ownership_path).unwrap()).unwrap();
        let report = remove_patch(&ownership, &roots, &backup).unwrap();
        assert_eq!(report.restored, 1);
        assert_eq!(fs::read(&destination).unwrap(), b"original");

        install_patch(&patch, &staging, &roots, &backup, &ownership_path).unwrap();
        fs::write(&destination, b"external-change").unwrap();
        let ownership: OwnershipManifest =
            serde_json::from_slice(&fs::read(&ownership_path).unwrap()).unwrap();
        let report = remove_patch(&ownership, &roots, &backup).unwrap();
        assert_eq!(report.skipped, 1);
        assert_eq!(fs::read(&destination).unwrap(), b"external-change");
    }

    #[test]
    fn removal_preflight_keeps_other_owned_files_untouched() {
        let temp = tempdir().unwrap();
        let roots = roots(temp.path());
        let backup = temp.path().join("backup");
        let safe = roots.addressables.join("safe/__data");
        let changed = roots.addressables.join("changed/__data");
        fs::create_dir_all(safe.parent().unwrap()).unwrap();
        fs::create_dir_all(changed.parent().unwrap()).unwrap();
        fs::write(&safe, b"patched-safe").unwrap();
        fs::write(&changed, b"external-change").unwrap();

        let ownership = OwnershipManifest {
            schema_version: 1,
            patch_version: "v1".into(),
            catalog_hash: "b".repeat(32),
            created_files: vec![
                OwnedCreatedFile {
                    target: InstallTarget::Addressables,
                    path: "safe/__data".into(),
                    installed_sha256: format!("{:x}", Sha256::digest(b"patched-safe")),
                },
                OwnedCreatedFile {
                    target: InstallTarget::Addressables,
                    path: "changed/__data".into(),
                    installed_sha256: format!("{:x}", Sha256::digest(b"patched-changed")),
                },
            ],
            modified_files: vec![],
        };

        let report = remove_patch(&ownership, &roots, &backup).unwrap();
        assert_eq!(report.removed, 0);
        assert_eq!(report.restored, 0);
        assert_eq!(report.skipped, 1);
        assert_eq!(fs::read(&safe).unwrap(), b"patched-safe");
        assert_eq!(fs::read(&changed).unwrap(), b"external-change");
    }

    #[test]
    fn already_restored_modified_file_is_idempotent() {
        let temp = tempdir().unwrap();
        let roots = roots(temp.path());
        fs::create_dir_all(&roots.game_data).unwrap();
        let destination = roots.game_data.join("data.unity3d");
        fs::write(&destination, b"original").unwrap();
        let ownership = OwnershipManifest {
            schema_version: 1,
            patch_version: "v1".into(),
            catalog_hash: "b".repeat(32),
            created_files: vec![],
            modified_files: vec![OwnedModifiedFile {
                target: InstallTarget::GameData,
                path: "data.unity3d".into(),
                original_sha256: format!("{:x}", Sha256::digest(b"original")),
                patched_sha256: format!("{:x}", Sha256::digest(b"patched")),
                backup_path: "game-data/data.unity3d".into(),
            }],
        };

        let report = remove_patch(&ownership, &roots, &temp.path().join("backup")).unwrap();
        assert_eq!(report.removed, 0);
        assert_eq!(report.restored, 0);
        assert_eq!(report.skipped, 0);
        assert_eq!(fs::read(&destination).unwrap(), b"original");
    }
}
