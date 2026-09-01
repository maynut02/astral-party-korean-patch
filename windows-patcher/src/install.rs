use std::fs;
use std::io::{self, Read, Write};
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
pub enum ApplyPhase {
    VerifyingStage,
    BackingUp,
    Copying,
    VerifyingInstalled,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApplyProgress {
    pub file_index: usize,
    pub file_count: usize,
    pub path: String,
    pub phase: ApplyPhase,
    pub current: u64,
    pub total: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RemoveIssueKind {
    ModifiedExternally,
    TargetMissing,
    BackupMissing,
    BackupHashMismatch,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RemoveIssue {
    pub target: InstallTarget,
    pub path: String,
    pub kind: RemoveIssueKind,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct RemoveIssueSummary {
    pub modified_externally: usize,
    pub target_missing: usize,
    pub backup_missing: usize,
    pub backup_hash_mismatch: usize,
}

impl RemoveIssueSummary {
    pub fn total(self) -> usize {
        self.modified_externally
            + self.target_missing
            + self.backup_missing
            + self.backup_hash_mismatch
    }
}

impl std::fmt::Display for RemoveIssueSummary {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "외부 변경 {}, 파일 누락 {}, 백업 누락 {}, 백업 손상 {}",
            self.modified_externally,
            self.target_missing,
            self.backup_missing,
            self.backup_hash_mismatch
        )
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RemoveReport {
    pub removed: usize,
    pub restored: usize,
    pub issues: Vec<RemoveIssue>,
}

impl RemoveReport {
    pub fn issue_summary(&self) -> RemoveIssueSummary {
        let mut summary = RemoveIssueSummary::default();
        for issue in &self.issues {
            match issue.kind {
                RemoveIssueKind::ModifiedExternally => summary.modified_externally += 1,
                RemoveIssueKind::TargetMissing => summary.target_missing += 1,
                RemoveIssueKind::BackupMissing => summary.backup_missing += 1,
                RemoveIssueKind::BackupHashMismatch => summary.backup_hash_mismatch += 1,
            }
        }
        summary
    }
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
    copy_replace_with_progress(source, destination, |_, _| {})
}

fn copy_replace_with_progress<F>(
    source: &Path,
    destination: &Path,
    mut progress: F,
) -> Result<(), io::Error>
where
    F: FnMut(u64, u64),
{
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)?;
    }
    let total = source.metadata()?.len();
    let temp = destination.with_extension("astral-install-tmp");
    let mut input = fs::File::open(source)?;
    let mut output = fs::File::create(&temp)?;
    let mut copied = 0_u64;
    let mut buffer = [0_u8; 128 * 1024];
    progress(0, total);
    loop {
        let read = input.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        output.write_all(&buffer[..read])?;
        copied += read as u64;
        progress(copied.min(total), total);
    }
    output.sync_all()?;
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
    install_patch_with_progress(
        manifest,
        staging_root,
        roots,
        backup_root,
        ownership_path,
        |_| {},
    )
}

pub fn install_patch_with_progress<F>(
    manifest: &PatchManifest,
    staging_root: &Path,
    roots: &InstallRoots,
    backup_root: &Path,
    ownership_path: &Path,
    mut progress: F,
) -> Result<InstallSummary, InstallError>
where
    F: FnMut(ApplyProgress),
{
    manifest.validate()?;
    let file_count = manifest.files.len();
    let total_size = manifest.files.iter().map(|file| file.size).sum();
    let mut completed_size = 0_u64;
    let mut ownership = OwnershipManifest {
        schema_version: 1,
        patch_version: manifest.patch.version.clone(),
        catalog_hash: manifest.game.catalog_hash.clone(),
        created_files: Vec::new(),
        modified_files: Vec::new(),
    };

    let result = (|| {
        for (index, file) in manifest.files.iter().enumerate() {
            let file_index = index + 1;
            let stage = staged_path(staging_root, file.target, &file.path)?;
            progress(ApplyProgress {
                file_index,
                file_count,
                path: file.path.clone(),
                phase: ApplyPhase::VerifyingStage,
                current: completed_size,
                total: total_size,
            });
            verify_file(&stage, file.size, &file.sha256)?;
            let destination = target_path(roots, file.target, &file.path)?;

            if destination.exists() {
                progress(ApplyProgress {
                    file_index,
                    file_count,
                    path: file.path.clone(),
                    phase: ApplyPhase::BackingUp,
                    current: completed_size,
                    total: total_size,
                });
                let backup = backup_path(backup_root, file.target, &file.path)?;
                let original_hash = match (&file.source_sha256, file.source_size) {
                    (Some(source_sha256), Some(source_size)) => {
                        verify_file(&destination, source_size, source_sha256)?;
                        verify_file(&backup, source_size, source_sha256)?;
                        source_sha256.clone()
                    }
                    (None, None) => {
                        let original_hash = sha256_file(&destination)?;
                        if let Some(parent) = backup.parent() {
                            fs::create_dir_all(parent)?;
                        }
                        fs::copy(&destination, &backup)?;
                        original_hash
                    }
                    _ => {
                        return Err(InstallError::Protocol(ProtocolError::UnsafePath(
                            "partial source restore metadata".into(),
                        )));
                    }
                };
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

            copy_replace_with_progress(&stage, &destination, |current, _| {
                progress(ApplyProgress {
                    file_index,
                    file_count,
                    path: file.path.clone(),
                    phase: ApplyPhase::Copying,
                    current: completed_size.saturating_add(current),
                    total: total_size,
                });
            })?;
            completed_size = completed_size.saturating_add(file.size);
            progress(ApplyProgress {
                file_index,
                file_count,
                path: file.path.clone(),
                phase: ApplyPhase::VerifyingInstalled,
                current: completed_size,
                total: total_size,
            });
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

pub fn installed_patch_change_count(
    ownership: &OwnershipManifest,
    roots: &InstallRoots,
) -> Result<usize, InstallError> {
    if ownership.schema_version != 1 {
        return Err(InstallError::OwnershipMismatch);
    }

    let mut changed = 0;
    for created in &ownership.created_files {
        let path = target_path(roots, created.target, &created.path)?;
        if !path.is_file() || sha256_file(&path)? != created.installed_sha256 {
            changed += 1;
        }
    }
    for modified in &ownership.modified_files {
        let path = target_path(roots, modified.target, &modified.path)?;
        if !path.is_file() || sha256_file(&path)? != modified.patched_sha256 {
            changed += 1;
        }
    }
    Ok(changed)
}

pub fn remove_patch(
    ownership: &OwnershipManifest,
    roots: &InstallRoots,
    backup_root: &Path,
) -> Result<RemoveReport, InstallError> {
    if ownership.schema_version != 1 {
        return Err(InstallError::OwnershipMismatch);
    }

    // Preflight every owned path before mutating anything. If any path is unsafe, leave all
    // remaining patch files untouched so uninstall/upgrade never becomes partial.
    let mut issues = Vec::new();
    for created in &ownership.created_files {
        let path = target_path(roots, created.target, &created.path)?;
        if path.exists() && sha256_file(&path)? != created.installed_sha256 {
            issues.push(RemoveIssue {
                target: created.target,
                path: created.path.clone(),
                kind: RemoveIssueKind::ModifiedExternally,
            });
        }
    }
    for modified in &ownership.modified_files {
        let destination = target_path(roots, modified.target, &modified.path)?;
        if !destination.exists() {
            issues.push(RemoveIssue {
                target: modified.target,
                path: modified.path.clone(),
                kind: RemoveIssueKind::TargetMissing,
            });
            continue;
        }
        let current_hash = sha256_file(&destination)?;
        if current_hash == modified.original_sha256 {
            // Already restored, for example after Steam verification or an interrupted cleanup.
            continue;
        }
        if current_hash != modified.patched_sha256 {
            issues.push(RemoveIssue {
                target: modified.target,
                path: modified.path.clone(),
                kind: RemoveIssueKind::ModifiedExternally,
            });
            continue;
        }
        validate_relative_path(&modified.backup_path)?;
        let backup = backup_root.join(&modified.backup_path);
        if !backup.is_file() {
            issues.push(RemoveIssue {
                target: modified.target,
                path: modified.path.clone(),
                kind: RemoveIssueKind::BackupMissing,
            });
            continue;
        }
        if sha256_file(&backup)? != modified.original_sha256 {
            issues.push(RemoveIssue {
                target: modified.target,
                path: modified.path.clone(),
                kind: RemoveIssueKind::BackupHashMismatch,
            });
        }
    }
    if !issues.is_empty() {
        return Ok(RemoveReport {
            removed: 0,
            restored: 0,
            issues,
        });
    }

    let mut report = RemoveReport {
        removed: 0,
        restored: 0,
        issues: Vec::new(),
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
            schema_version: 2,
            patch: PatchMetadata {
                version: "v1".into(),
                channel: "release".into(),
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
            download_url: "https://example.test/created.gz".into(),
            download_sha256: "d".repeat(64),
            download_size: 5,
            compression: "gzip".into(),
            sha256: hash,
            size: payload.len() as u64,
            source_download_url: None,
            source_download_sha256: None,
            source_download_size: None,
            source_sha256: None,
            source_size: None,
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
    fn install_progress_reports_copy_bytes() {
        let temp = tempdir().unwrap();
        let staging = temp.path().join("staging");
        let roots = roots(temp.path());
        let backup = temp.path().join("backup");
        let ownership_path = temp.path().join("installed.json");
        let payload = vec![b'x'; 512 * 1024];
        let hash = format!("{:x}", Sha256::digest(&payload));
        let stage = staging.join("game-data/data.unity3d");
        fs::create_dir_all(stage.parent().unwrap()).unwrap();
        fs::write(&stage, &payload).unwrap();
        let patch = manifest(ManifestFile {
            target: InstallTarget::GameData,
            path: "data.unity3d".into(),
            operation: "replace".into(),
            download_url: "https://example.test/data.gz".into(),
            download_sha256: "d".repeat(64),
            download_size: 5,
            compression: "gzip".into(),
            sha256: hash,
            size: payload.len() as u64,
            source_download_url: None,
            source_download_sha256: None,
            source_download_size: None,
            source_sha256: None,
            source_size: None,
        });
        let mut events = Vec::new();

        install_patch_with_progress(
            &patch,
            &staging,
            &roots,
            &backup,
            &ownership_path,
            |event| events.push(event),
        )
        .unwrap();

        let copying = events
            .iter()
            .filter(|event| event.phase == ApplyPhase::Copying)
            .collect::<Vec<_>>();
        assert!(copying.len() > 2);
        assert_eq!(copying.first().unwrap().current, 0);
        assert_eq!(copying.last().unwrap().current, payload.len() as u64);
        assert_eq!(copying.last().unwrap().total, payload.len() as u64);
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
            download_url: "https://example.test/replaced.gz".into(),
            download_sha256: "d".repeat(64),
            download_size: 5,
            compression: "gzip".into(),
            sha256: hash,
            size: payload.len() as u64,
            source_download_url: None,
            source_download_sha256: None,
            source_download_size: None,
            source_sha256: None,
            source_size: None,
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
        assert_eq!(report.issues.len(), 1);
        assert_eq!(fs::read(&destination).unwrap(), b"external-change");
    }

    #[test]
    fn release_restore_backup_requires_matching_game_original() {
        let temp = tempdir().unwrap();
        let staging = temp.path().join("staging");
        let roots = roots(temp.path());
        fs::create_dir_all(&roots.game_data).unwrap();
        let destination = roots.game_data.join("data.unity3d");
        fs::write(&destination, b"modified").unwrap();
        let backup = temp.path().join("backup/game-data/data.unity3d");
        fs::create_dir_all(backup.parent().unwrap()).unwrap();
        fs::write(&backup, b"original").unwrap();
        let ownership_path = temp.path().join("installed.json");
        let payload = b"patched";
        let payload_hash = format!("{:x}", Sha256::digest(payload));
        let original_hash = format!("{:x}", Sha256::digest(b"original"));
        let stage = staging.join("game-data/data.unity3d");
        fs::create_dir_all(stage.parent().unwrap()).unwrap();
        fs::write(&stage, payload).unwrap();
        let patch = manifest(ManifestFile {
            target: InstallTarget::GameData,
            path: "data.unity3d".into(),
            operation: "replace".into(),
            download_url: "https://example.test/replaced.gz".into(),
            download_sha256: "d".repeat(64),
            download_size: 5,
            compression: "gzip".into(),
            sha256: payload_hash,
            size: payload.len() as u64,
            source_download_url: Some("https://example.test/original.gz".into()),
            source_download_sha256: Some("e".repeat(64)),
            source_download_size: Some(5),
            source_sha256: Some(original_hash),
            source_size: Some(8),
        });

        let error = install_patch(
            &patch,
            &staging,
            &roots,
            &temp.path().join("backup"),
            &ownership_path,
        )
        .unwrap_err();
        assert!(matches!(error, InstallError::HashMismatch(_, _, _)));
        assert_eq!(fs::read(&destination).unwrap(), b"modified");
        assert_eq!(fs::read(&backup).unwrap(), b"original");
        assert!(!ownership_path.exists());
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
        assert_eq!(report.issues.len(), 1);
        assert_eq!(fs::read(&safe).unwrap(), b"patched-safe");
        assert_eq!(fs::read(&changed).unwrap(), b"external-change");
    }

    #[test]
    fn removal_diagnostics_distinguish_missing_target_backup_and_corrupt_backup() {
        let temp = tempdir().unwrap();
        let roots = roots(temp.path());
        fs::create_dir_all(&roots.game_data).unwrap();
        let backup_root = temp.path().join("backup");
        fs::create_dir_all(backup_root.join("game-data")).unwrap();

        let patched_hash = format!("{:x}", Sha256::digest(b"patched"));
        let original_hash = format!("{:x}", Sha256::digest(b"original"));
        fs::write(roots.game_data.join("backup-missing.bin"), b"patched").unwrap();
        fs::write(roots.game_data.join("backup-corrupt.bin"), b"patched").unwrap();
        fs::write(
            backup_root.join("game-data/backup-corrupt.bin"),
            b"not-original",
        )
        .unwrap();

        let ownership = OwnershipManifest {
            schema_version: 1,
            patch_version: "v1".into(),
            catalog_hash: "b".repeat(32),
            created_files: vec![],
            modified_files: vec![
                OwnedModifiedFile {
                    target: InstallTarget::GameData,
                    path: "target-missing.bin".into(),
                    original_sha256: original_hash.clone(),
                    patched_sha256: patched_hash.clone(),
                    backup_path: "game-data/target-missing.bin".into(),
                },
                OwnedModifiedFile {
                    target: InstallTarget::GameData,
                    path: "backup-missing.bin".into(),
                    original_sha256: original_hash.clone(),
                    patched_sha256: patched_hash.clone(),
                    backup_path: "game-data/backup-missing.bin".into(),
                },
                OwnedModifiedFile {
                    target: InstallTarget::GameData,
                    path: "backup-corrupt.bin".into(),
                    original_sha256: original_hash,
                    patched_sha256: patched_hash,
                    backup_path: "game-data/backup-corrupt.bin".into(),
                },
            ],
        };

        let report = remove_patch(&ownership, &roots, &backup_root).unwrap();
        assert_eq!(
            report.issue_summary(),
            RemoveIssueSummary {
                modified_externally: 0,
                target_missing: 1,
                backup_missing: 1,
                backup_hash_mismatch: 1,
            }
        );
        assert_eq!(report.issues.len(), 3);
        assert_eq!(
            fs::read(roots.game_data.join("backup-missing.bin")).unwrap(),
            b"patched"
        );
        assert_eq!(
            fs::read(roots.game_data.join("backup-corrupt.bin")).unwrap(),
            b"patched"
        );
    }

    #[test]
    fn installed_patch_change_count_detects_missing_and_restored_files() {
        let temp = tempdir().unwrap();
        let roots = roots(temp.path());
        fs::create_dir_all(&roots.addressables).unwrap();
        fs::create_dir_all(&roots.game_data).unwrap();
        let created = roots.addressables.join("created/__data");
        fs::create_dir_all(created.parent().unwrap()).unwrap();
        fs::write(&created, b"patched-created").unwrap();
        let modified = roots.game_data.join("data.unity3d");
        fs::write(&modified, b"patched-modified").unwrap();
        let ownership = OwnershipManifest {
            schema_version: 1,
            patch_version: "v1".into(),
            catalog_hash: "b".repeat(32),
            created_files: vec![OwnedCreatedFile {
                target: InstallTarget::Addressables,
                path: "created/__data".into(),
                installed_sha256: format!("{:x}", Sha256::digest(b"patched-created")),
            }],
            modified_files: vec![OwnedModifiedFile {
                target: InstallTarget::GameData,
                path: "data.unity3d".into(),
                original_sha256: format!("{:x}", Sha256::digest(b"original")),
                patched_sha256: format!("{:x}", Sha256::digest(b"patched-modified")),
                backup_path: "game-data/data.unity3d".into(),
            }],
        };

        assert_eq!(installed_patch_change_count(&ownership, &roots).unwrap(), 0);
        fs::remove_file(&created).unwrap();
        fs::write(&modified, b"original").unwrap();
        assert_eq!(installed_patch_change_count(&ownership, &roots).unwrap(), 2);
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
        assert!(report.issues.is_empty());
        assert_eq!(fs::read(&destination).unwrap(), b"original");
    }
}
