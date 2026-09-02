use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};

use flate2::read::GzDecoder;
use reqwest::blocking::Client;
use sha2::{Digest, Sha256};
use thiserror::Error;

use crate::protocol::{
    InstallTarget, ManifestFile, PatchManifest, ProtocolError, ReleaseIndex, ReleaseIndexEntry,
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
    #[error("payload size mismatch for {path}: expected {expected}, actual {actual}")]
    PayloadSizeMismatch {
        path: PathBuf,
        expected: u64,
        actual: u64,
    },
    #[error("payload SHA-256 mismatch for {path}: expected {expected}, actual {actual}")]
    PayloadHashMismatch {
        path: PathBuf,
        expected: String,
        actual: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StageProgress {
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
        let mut index: ReleaseIndex = serde_json::from_slice(&raw)?;
        index.releases.retain(|entry| entry.channel == "release");
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
        self.stage_manifest_files_with_progress(manifest, staging_root, |_| {})
    }

    pub fn stage_manifest_files_with_progress<F>(
        &self,
        manifest: &PatchManifest,
        staging_root: &Path,
        mut progress: F,
    ) -> Result<Vec<PathBuf>, NetworkError>
    where
        F: FnMut(StageProgress),
    {
        manifest.validate()?;
        let file_count = manifest.files.len();
        let total_download = manifest.files.iter().map(|file| file.download_size).sum();
        let mut completed_download = 0_u64;
        let mut staged = Vec::with_capacity(file_count);
        for (index, file) in manifest.files.iter().enumerate() {
            validate_relative_path(&file.path)?;
            let destination = staging_root.join(target_dir(file.target)).join(&file.path);
            if let Some(parent) = destination.parent() {
                fs::create_dir_all(parent)?;
            }
            let download_temp = destination.with_extension("astral-download-gz");
            let payload_temp = destination.with_extension("astral-payload-tmp");
            let file_index = index + 1;
            let file_name = download_file_name(&file.download_url);
            let result = (|| {
                self.download_verified_with_progress(
                    &file.download_url,
                    &download_temp,
                    file.download_size,
                    &file.download_sha256,
                    |current| {
                        progress(StageProgress::Downloading {
                            file_index,
                            file_count,
                            file_name: file_name.clone(),
                            current: completed_download.saturating_add(current),
                            total: total_download,
                        });
                    },
                )?;
                completed_download = completed_download.saturating_add(file.download_size);
                decompress_gzip_verified_with_progress(
                    &download_temp,
                    &payload_temp,
                    file.size,
                    &file.sha256,
                    |current| {
                        progress(StageProgress::Extracting {
                            file_index,
                            file_count,
                            file_name: file.path.clone(),
                            current,
                            total: file.size,
                        });
                    },
                )?;
                if destination.exists() {
                    fs::remove_file(&destination)?;
                }
                fs::rename(&payload_temp, &destination)?;
                Ok::<(), NetworkError>(())
            })();
            let _ = fs::remove_file(&download_temp);
            if let Err(error) = result {
                let _ = fs::remove_file(&payload_temp);
                return Err(error);
            }
            staged.push(destination);
        }
        Ok(staged)
    }

    pub fn download_original_file(
        &self,
        file: &ManifestFile,
        destination: &Path,
    ) -> Result<(), NetworkError> {
        let (url, download_sha256, download_size, source_sha256, source_size) = match (
            &file.source_download_url,
            &file.source_download_sha256,
            file.source_download_size,
            &file.source_sha256,
            file.source_size,
        ) {
            (
                Some(url),
                Some(download_sha256),
                Some(download_size),
                Some(source_sha256),
                Some(source_size),
            ) => (
                url,
                download_sha256,
                download_size,
                source_sha256,
                source_size,
            ),
            _ => {
                return Err(
                    ProtocolError::UnsafePath("source restore metadata missing".into()).into(),
                );
            }
        };
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)?;
        }
        let transport = destination.with_extension("astral-original-gz");
        let payload = destination.with_extension("astral-original-tmp");
        let result = (|| {
            self.download_verified_with_progress(
                url,
                &transport,
                download_size,
                download_sha256,
                |_| {},
            )?;
            decompress_gzip_verified_with_progress(
                &transport,
                &payload,
                source_size,
                source_sha256,
                |_| {},
            )?;
            if destination.exists() {
                fs::remove_file(destination)?;
            }
            fs::rename(&payload, destination)?;
            Ok::<(), NetworkError>(())
        })();
        let _ = fs::remove_file(&transport);
        if result.is_err() {
            let _ = fs::remove_file(&payload);
        }
        result
    }

    fn download_verified_with_progress<F>(
        &self,
        url: &str,
        destination: &Path,
        expected_size: u64,
        expected_hash: &str,
        mut progress: F,
    ) -> Result<(), NetworkError>
    where
        F: FnMut(u64),
    {
        let mut response = self.client.get(url).send()?.error_for_status()?;
        let mut output = fs::File::create(destination)?;
        let mut hasher = Sha256::new();
        let mut total = 0_u64;
        let mut buffer = [0_u8; 128 * 1024];
        progress(0);
        loop {
            let read = response.read(&mut buffer)?;
            if read == 0 {
                break;
            }
            output.write_all(&buffer[..read])?;
            hasher.update(&buffer[..read]);
            total += read as u64;
            progress(total.min(expected_size));
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

fn decompress_gzip_verified_with_progress<F>(
    source: &Path,
    destination: &Path,
    expected_size: u64,
    expected_hash: &str,
    mut progress: F,
) -> Result<(), NetworkError>
where
    F: FnMut(u64),
{
    let input = fs::File::open(source)?;
    let mut decoder = GzDecoder::new(input);
    let mut output = fs::File::create(destination)?;
    let mut hasher = Sha256::new();
    let mut total = 0_u64;
    let mut buffer = [0_u8; 128 * 1024];
    progress(0);
    loop {
        let read = decoder.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        output.write_all(&buffer[..read])?;
        hasher.update(&buffer[..read]);
        total += read as u64;
        progress(total.min(expected_size));
        if total > expected_size {
            return Err(NetworkError::PayloadSizeMismatch {
                path: destination.to_owned(),
                expected: expected_size,
                actual: total,
            });
        }
    }
    output.sync_all()?;
    if total != expected_size {
        return Err(NetworkError::PayloadSizeMismatch {
            path: destination.to_owned(),
            expected: expected_size,
            actual: total,
        });
    }
    let actual = format!("{:x}", hasher.finalize());
    if actual != expected_hash {
        return Err(NetworkError::PayloadHashMismatch {
            path: destination.to_owned(),
            expected: expected_hash.to_owned(),
            actual,
        });
    }
    Ok(())
}

fn download_file_name(url: &str) -> String {
    url.rsplit('/')
        .next()
        .filter(|value| !value.is_empty())
        .unwrap_or(url)
        .to_owned()
}

fn target_dir(target: InstallTarget) -> &'static str {
    match target {
        InstallTarget::Addressables => "addressables",
        InstallTarget::GameData => "game-data",
    }
}

#[cfg(test)]
mod tests {
    use flate2::Compression;
    use flate2::write::GzEncoder;
    use tempfile::tempdir;

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
                channel: "release".into(),
                patch_version: "v1".into(),
                manifest_url: "https://example.test/manifest.json".into(),
                manifest_sha256: "c".repeat(64),
            }],
        };
        assert!(
            index
                .resolve("INT_STEAM", "3.2.0", &"b".repeat(32), "release")
                .is_some()
        );
    }

    #[test]
    fn manifest_compatibility_fields_are_explicit() {
        let manifest = PatchManifest {
            schema_version: 2,
            patch: PatchMetadata {
                version: "v1".into(),
                channel: "release".into(),
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

    #[test]
    fn gzip_payload_is_verified_after_decompression() {
        let temp = tempdir().unwrap();
        let source = temp.path().join("payload.gz");
        let destination = temp.path().join("payload.bin");
        let payload = b"patched-unity-payload";
        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(payload).unwrap();
        fs::write(&source, encoder.finish().unwrap()).unwrap();
        let hash = format!("{:x}", Sha256::digest(payload));

        let mut progress = Vec::new();
        decompress_gzip_verified_with_progress(
            &source,
            &destination,
            payload.len() as u64,
            &hash,
            |current| progress.push(current),
        )
        .unwrap();
        assert_eq!(progress.first(), Some(&0));
        assert_eq!(progress.last(), Some(&(payload.len() as u64)));
        assert_eq!(fs::read(destination).unwrap(), payload);
    }
}
