use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};

use reqwest::blocking::Client;
use sha2::{Digest, Sha256};
use thiserror::Error;

use crate::protocol::{
    InstallTarget, PatchManifest, ProtocolError, ReleaseIndex, ReleaseIndexEntry,
    validate_relative_path,
};

const MAX_METADATA_BYTES: u64 = 8 * 1024 * 1024;

#[derive(Debug, Error)]
pub enum NetworkError {
    #[error("HTTP error: {0}")]
    Http(#[from] reqwest::Error),
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("protocol error: {0}")]
    Protocol(#[from] ProtocolError),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("metadata response exceeds {0} bytes")]
    MetadataTooLarge(u64),
    #[error("no compatible patch release was found")]
    NoCompatibleRelease,
    #[error("manifest SHA-256 mismatch: expected {expected}, actual {actual}")]
    ManifestHashMismatch { expected: String, actual: String },
    #[error("manifest does not match selected release index entry")]
    ManifestCompatibilityMismatch,
    #[error("download size mismatch for {path}: expected {expected}, actual {actual}")]
    DownloadSizeMismatch {
        path: PathBuf,
        expected: u64,
        actual: u64,
    },
    #[error("download SHA-256 mismatch for {path}: expected {expected}, actual {actual}")]
    DownloadHashMismatch {
        path: PathBuf,
        expected: String,
        actual: String,
    },
}

#[derive(Debug, Clone)]
pub struct ReleaseClient {
    client: Client,
}

impl ReleaseClient {
    pub fn new(user_agent: &str) -> Result<Self, NetworkError> {
        let client = Client::builder().user_agent(user_agent).build()?;
        Ok(Self { client })
    }

    fn get_metadata(&self, url: &str) -> Result<Vec<u8>, NetworkError> {
        let response = self.client.get(url).send()?.error_for_status()?;
        if response
            .content_length()
            .is_some_and(|size| size > MAX_METADATA_BYTES)
        {
            return Err(NetworkError::MetadataTooLarge(MAX_METADATA_BYTES));
        }
        let mut bytes = Vec::new();
        response
            .take(MAX_METADATA_BYTES + 1)
            .read_to_end(&mut bytes)?;
        if bytes.len() as u64 > MAX_METADATA_BYTES {
            return Err(NetworkError::MetadataTooLarge(MAX_METADATA_BYTES));
        }
        Ok(bytes)
    }

    pub fn fetch_release_index(&self, url: &str) -> Result<ReleaseIndex, NetworkError> {
        let raw = self.get_metadata(url)?;
        let index: ReleaseIndex = serde_json::from_slice(&raw)?;
        index.validate()?;
        Ok(index)
    }

    pub fn fetch_compatible_manifest(
        &self,
        index: &ReleaseIndex,
        route: &str,
        game_version: &str,
        catalog_hash: &str,
        channel: &str,
    ) -> Result<(ReleaseIndexEntry, PatchManifest), NetworkError> {
        let entry = index
            .resolve(route, game_version, catalog_hash, channel)
            .cloned()
            .ok_or(NetworkError::NoCompatibleRelease)?;
        let raw = self.get_metadata(&entry.manifest_url)?;
        let actual = format!("{:x}", Sha256::digest(&raw));
        if actual != entry.manifest_sha256 {
            return Err(NetworkError::ManifestHashMismatch {
                expected: entry.manifest_sha256.clone(),
                actual,
            });
        }
        let manifest: PatchManifest = serde_json::from_slice(&raw)?;
        manifest.validate()?;
        if manifest.patch.route != entry.route
            || manifest.patch.channel != entry.channel
            || manifest.patch.version != entry.patch_version
            || manifest.game.version != entry.game_version
            || manifest.game.revision != entry.revision
            || manifest.game.catalog_hash != entry.catalog_hash
        {
            return Err(NetworkError::ManifestCompatibilityMismatch);
        }
        Ok((entry, manifest))
    }

    pub fn stage_manifest_files(
        &self,
        manifest: &PatchManifest,
        staging_root: &Path,
    ) -> Result<Vec<PathBuf>, NetworkError> {
        manifest.validate()?;
        let mut staged = Vec::with_capacity(manifest.files.len());
        for file in &manifest.files {
            validate_relative_path(&file.path)?;
            let destination = staging_root.join(target_dir(file.target)).join(&file.path);
            if let Some(parent) = destination.parent() {
                fs::create_dir_all(parent)?;
            }
            let temp = destination.with_extension("astral-download-tmp");
            let result = self.download_verified(&file.download_url, &temp, file.size, &file.sha256);
            if let Err(error) = result {
                let _ = fs::remove_file(&temp);
                return Err(error);
            }
            if destination.exists() {
                fs::remove_file(&destination)?;
            }
            fs::rename(&temp, &destination)?;
            staged.push(destination);
        }
        Ok(staged)
    }

    fn download_verified(
        &self,
        url: &str,
        destination: &Path,
        expected_size: u64,
        expected_hash: &str,
    ) -> Result<(), NetworkError> {
        let mut response = self.client.get(url).send()?.error_for_status()?;
        let mut output = fs::File::create(destination)?;
        let mut hasher = Sha256::new();
        let mut total = 0_u64;
        let mut buffer = [0_u8; 128 * 1024];
        loop {
            let read = response.read(&mut buffer)?;
            if read == 0 {
                break;
            }
            output.write_all(&buffer[..read])?;
            hasher.update(&buffer[..read]);
            total += read as u64;
            if total > expected_size {
                return Err(NetworkError::DownloadSizeMismatch {
                    path: destination.to_owned(),
                    expected: expected_size,
                    actual: total,
                });
            }
        }
        output.sync_all()?;
        if total != expected_size {
            return Err(NetworkError::DownloadSizeMismatch {
                path: destination.to_owned(),
                expected: expected_size,
                actual: total,
            });
        }
        let actual = format!("{:x}", hasher.finalize());
        if actual != expected_hash {
            return Err(NetworkError::DownloadHashMismatch {
                path: destination.to_owned(),
                expected: expected_hash.to_owned(),
                actual,
            });
        }
        Ok(())
    }
}

fn target_dir(target: InstallTarget) -> &'static str {
    match target {
        InstallTarget::Addressables => "addressables",
        InstallTarget::GameData => "game-data",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::protocol::{PatchMetadata, TargetGame};

    #[test]
    fn release_index_resolves_without_local_revision() {
        let index = ReleaseIndex {
            schema_version: 1,
            releases: vec![ReleaseIndexEntry {
                route: "INT_STEAM".into(),
                game_version: "3.2.0".into(),
                revision: "1042".into(),
                catalog_hash: "b".repeat(32),
                channel: "stable".into(),
                patch_version: "v1".into(),
                manifest_url: "https://example.test/manifest.json".into(),
                manifest_sha256: "c".repeat(64),
            }],
        };
        assert!(
            index
                .resolve("INT_STEAM", "3.2.0", &"b".repeat(32), "stable")
                .is_some()
        );
    }

    #[test]
    fn manifest_compatibility_fields_are_explicit() {
        let manifest = PatchManifest {
            schema_version: 1,
            patch: PatchMetadata {
                version: "v1".into(),
                channel: "preview".into(),
                route: "INT_STEAM".into(),
                build_id: "build".into(),
                translation_fingerprint: "a".repeat(64),
            },
            game: TargetGame {
                version: "3.2.0".into(),
                revision: "1042".into(),
                catalog_hash: "b".repeat(32),
            },
            files: vec![],
        };
        assert_eq!(manifest.game.catalog_hash.len(), 32);
    }
}
