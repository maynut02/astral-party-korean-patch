use serde::Serialize;

use crate::game::discover_windows_installation;
use crate::service::{
    InstallOutcome, PatcherPaths, install_latest_compatible, install_roots, installed_patch_info,
    remove_installed_patch,
};

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct GameStatus {
    game_root: String,
    game_data_root: String,
    addressables_root: String,
    game_version: String,
    catalog_hash: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ActionResult {
    kind: String,
    message: String,
}

#[tauri::command]
fn detect_game() -> Result<GameStatus, String> {
    let game = discover_windows_installation().map_err(|error| error.to_string())?;
    Ok(GameStatus {
        game_root: game.game_root.to_string_lossy().into_owned(),
        game_data_root: game.game_data_root.to_string_lossy().into_owned(),
        addressables_root: game.addressables_root.to_string_lossy().into_owned(),
        game_version: game.catalog.version,
        catalog_hash: game.catalog.hash,
    })
}

#[tauri::command]
fn get_installed_patch() -> Result<Option<crate::service::InstalledPatchInfo>, String> {
    let paths = PatcherPaths::windows_default().map_err(|error| error.to_string())?;
    installed_patch_info(&paths.ownership_path).map_err(|error| error.to_string())
}

#[tauri::command]
async fn install_latest(
    release_index_url: String,
    channel: String,
) -> Result<ActionResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let paths = PatcherPaths::windows_default().map_err(|error| error.to_string())?;
        let outcome = install_latest_compatible(&release_index_url, &channel, &paths)
            .map_err(|error| error.to_string())?;
        Ok(match outcome {
            InstallOutcome::AlreadyInstalled(info) => ActionResult {
                kind: "already-installed".into(),
                message: format!("{} 패치가 이미 설치되어 있습니다.", info.patch_version),
            },
            InstallOutcome::Installed(summary) => ActionResult {
                kind: "installed".into(),
                message: format!(
                    "패치 설치가 완료되었습니다. 새 파일 {}개, 교체 파일 {}개",
                    summary.created, summary.modified
                ),
            },
        })
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn remove_installed() -> Result<ActionResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let game = discover_windows_installation().map_err(|error| error.to_string())?;
        let roots = install_roots(&game);
        let paths = PatcherPaths::windows_default().map_err(|error| error.to_string())?;
        let report = remove_installed_patch(&paths, &roots).map_err(|error| error.to_string())?;
        Ok(match report {
            None => ActionResult {
                kind: "not-installed".into(),
                message: "설치된 패치 기록이 없습니다.".into(),
            },
            Some(report) => ActionResult {
                kind: "removed".into(),
                message: format!(
                    "패치를 제거했습니다. 삭제 {}개, 복구 {}개",
                    report.removed, report.restored
                ),
            },
        })
    })
    .await
    .map_err(|error| error.to_string())?
}

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            detect_game,
            get_installed_patch,
            install_latest,
            remove_installed
        ])
        .run(tauri::generate_context!())
        .expect("failed to run Astral Party Korean Patcher");
}
