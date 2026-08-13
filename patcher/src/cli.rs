use std::io::{self, Write};
use std::path::{Path, PathBuf};

use thiserror::Error;

use crate::registration::RegistrationError;
#[cfg(windows)]
use crate::registration::ensure_self_installed_and_registered;
use crate::service::{
    InstallOutcome, PatcherPaths, ServiceError, install_latest_compatible, install_roots,
    installed_patch_info, remove_installed_patch,
};
use crate::settings::{AppSettings, SettingsError};
use crate::uri::{UriAction, UriError};

const FALLBACK_RELEASE_INDEX_URL: &str = "https://github.com/maynut02/astral-party-korean-patch/releases/download/patch-index/release-index.json";

#[derive(Debug, Error)]
pub enum CliError {
    #[error(transparent)]
    Service(#[from] ServiceError),
    #[error(transparent)]
    Settings(#[from] SettingsError),
    #[error(transparent)]
    Registration(#[from] RegistrationError),
    #[error(transparent)]
    Uri(#[from] UriError),
    #[error("I/O error: {0}")]
    Io(#[from] io::Error),
    #[error("AstralAutoPatcher supports Windows only")]
    UnsupportedPlatform,
}

fn release_index_url() -> &'static str {
    option_env!("ASTRAL_PATCH_INDEX_URL")
        .filter(|value| !value.trim().is_empty())
        .unwrap_or(FALLBACK_RELEASE_INDEX_URL)
}

fn prompt(label: &str) -> Result<String, io::Error> {
    print!("{label}");
    io::stdout().flush()?;
    let mut input = String::new();
    io::stdin().read_line(&mut input)?;
    Ok(input.trim().to_owned())
}

fn clean_input_path(value: &str) -> PathBuf {
    let trimmed = value.trim();
    let unquoted = trimmed
        .strip_prefix('"')
        .and_then(|value| value.strip_suffix('"'))
        .unwrap_or(trimmed);
    PathBuf::from(unquoted)
}

fn display_path(path: Option<&Path>) -> String {
    path.map(|value| value.display().to_string())
        .unwrap_or_else(|| "미설정".into())
}

fn pause() {
    let _ = prompt("\nEnter 키를 누르면 종료합니다...");
}

fn load_initial_settings(paths: &PatcherPaths) -> Result<AppSettings, CliError> {
    let existed = paths.settings_path.is_file();
    let mut settings = AppSettings::load(&paths.settings_path)?;
    let changed = settings.auto_detect_missing();
    if changed || !existed {
        settings.save(&paths.settings_path)?;
    }
    Ok(settings)
}

fn print_status(paths: &PatcherPaths, settings: &AppSettings) {
    println!("\nAstralAutoPatcher v{}", env!("CARGO_PKG_VERSION"));
    println!("----------------------------------------");
    println!(
        "Steam 경로   : {}",
        display_path(settings.steam_game_root.as_deref())
    );
    println!(
        "LocalLow 경로: {}",
        display_path(settings.locallow_root.as_deref())
    );
    println!("패치 채널    : {}", settings.channel);
    match settings.installation() {
        Ok(game) => {
            println!("게임 버전    : {}", game.catalog.version);
            println!("Catalog      : {}", game.catalog.hash);
        }
        Err(error) => println!("게임 상태    : 감지 실패 ({error})"),
    }
    match installed_patch_info(&paths.ownership_path) {
        Ok(Some(info)) => println!("설치 패치    : {}", info.patch_version),
        Ok(None) => println!("설치 패치    : 미설치"),
        Err(error) => println!("설치 패치    : 상태 확인 실패 ({error})"),
    }
    println!("----------------------------------------");
}

fn install(paths: &PatcherPaths, settings: &AppSettings) -> Result<(), CliError> {
    let game = settings.installation()?;
    println!("호환 패치를 확인하고 다운로드합니다...");
    match install_latest_compatible(release_index_url(), &settings.channel, paths, &game)? {
        InstallOutcome::AlreadyInstalled(info) => {
            println!("{} 패치가 이미 설치되어 있습니다.", info.patch_version);
        }
        InstallOutcome::Installed(summary) => {
            println!(
                "패치 설치가 완료되었습니다. 새 파일 {}개, 교체 파일 {}개",
                summary.created, summary.modified
            );
        }
    }
    Ok(())
}

fn remove(paths: &PatcherPaths, settings: &AppSettings) -> Result<(), CliError> {
    let game = settings.installation()?;
    let roots = install_roots(&game);
    println!("설치 기록을 검증하고 패치를 제거합니다...");
    match remove_installed_patch(paths, &roots)? {
        None => println!("설치된 패치 기록이 없습니다."),
        Some(report) => println!(
            "패치를 제거했습니다. 삭제 {}개, 복구 {}개",
            report.removed, report.restored
        ),
    }
    Ok(())
}

fn path_changes_allowed(paths: &PatcherPaths) -> Result<bool, CliError> {
    if installed_patch_info(&paths.ownership_path)?.is_some() {
        println!("패치가 설치된 상태에서는 게임 경로를 변경할 수 없습니다.");
        println!("먼저 메인 메뉴에서 패치를 제거한 뒤 다시 시도하세요.");
        return Ok(false);
    }
    Ok(true)
}

fn settings_menu(paths: &PatcherPaths, settings: &mut AppSettings) -> Result<(), CliError> {
    loop {
        println!("\nAstralAutoPatcher 설정");
        println!("----------------------------------------");
        println!(
            "1. Steam 게임 경로   [{}]",
            display_path(settings.steam_game_root.as_deref())
        );
        println!(
            "2. LocalLow 게임 경로 [{}]",
            display_path(settings.locallow_root.as_deref())
        );
        println!("3. 패치 채널          [{}]", settings.channel);
        println!("4. 게임 경로 자동 감지");
        println!("0. 돌아가기");
        println!("----------------------------------------");
        match prompt("선택 > ")?.as_str() {
            "1" => {
                if !path_changes_allowed(paths)? {
                    continue;
                }
                let value = prompt("Steam의 'Astral Party' 폴더 경로 > ")?;
                if value.is_empty() {
                    continue;
                }
                let path = clean_input_path(&value);
                match settings.set_steam_game_root(&path) {
                    Ok(()) => {
                        settings.save(&paths.settings_path)?;
                        println!("Steam 경로를 저장했습니다.");
                    }
                    Err(error) => println!("유효한 Steam 게임 경로가 아닙니다: {error}"),
                }
            }
            "2" => {
                if !path_changes_allowed(paths)? {
                    continue;
                }
                let value = prompt("LocalLow의 'AstralParty_INT' 폴더 경로 > ")?;
                if value.is_empty() {
                    continue;
                }
                let path = clean_input_path(&value);
                match settings.set_locallow_root(&path) {
                    Ok(()) => {
                        settings.save(&paths.settings_path)?;
                        println!("LocalLow 경로를 저장했습니다.");
                    }
                    Err(error) => println!("유효한 LocalLow 경로가 아닙니다: {error}"),
                }
            }
            "3" => {
                println!("1. stable");
                println!("2. preview");
                match prompt("선택 > ")?.as_str() {
                    "1" => settings.set_channel("stable")?,
                    "2" => settings.set_channel("preview")?,
                    _ => {
                        println!("잘못된 선택입니다.");
                        continue;
                    }
                }
                settings.save(&paths.settings_path)?;
                println!("패치 채널을 저장했습니다.");
            }
            "4" => {
                if !path_changes_allowed(paths)? {
                    continue;
                }
                match settings.redetect_all() {
                    Ok(()) => {
                        settings.save(&paths.settings_path)?;
                        println!("Steam과 LocalLow 경로를 다시 감지했습니다.");
                    }
                    Err(error) => println!("자동 감지에 실패했습니다: {error}"),
                }
            }
            "0" => return Ok(()),
            _ => println!("잘못된 선택입니다."),
        }
    }
}

fn main_menu(paths: &PatcherPaths, settings: &mut AppSettings) -> Result<(), CliError> {
    loop {
        print_status(paths, settings);
        println!("1. 패치 설치 / 업데이트");
        println!("2. 패치 제거");
        println!("3. 프로그램 설정");
        println!("0. 종료");
        match prompt("\n선택 > ")?.as_str() {
            "1" => {
                if let Err(error) = install(paths, settings) {
                    println!("패치 설치 실패: {error}");
                }
            }
            "2" => {
                if let Err(error) = remove(paths, settings) {
                    println!("패치 제거 실패: {error}");
                }
            }
            "3" => settings_menu(paths, settings)?,
            "0" => return Ok(()),
            _ => println!("잘못된 선택입니다."),
        }
    }
}

#[cfg(windows)]
pub fn run() -> Result<(), CliError> {
    let paths = PatcherPaths::windows_default()?;
    let installed_exe = ensure_self_installed_and_registered(&paths.state_root)?;
    let mut settings = load_initial_settings(&paths)?;

    if std::env::current_exe()
        .ok()
        .is_some_and(|current| current != installed_exe)
    {
        println!("AstralAutoPatcher를 다음 위치에 등록했습니다:");
        println!("{}", installed_exe.display());
        println!("웹사이트 연동 프로토콜: astral://");
    }

    let uri = std::env::args().nth(1);
    let Some(uri) = uri else {
        return main_menu(&paths, &mut settings);
    };
    let action = UriAction::parse(&uri)?;
    match action {
        UriAction::Menu => main_menu(&paths, &mut settings),
        UriAction::Install => {
            let result = install(&paths, &settings);
            if let Err(error) = &result {
                println!("패치 설치 실패: {error}");
            }
            pause();
            result
        }
        UriAction::Remove => {
            let result = remove(&paths, &settings);
            if let Err(error) = &result {
                println!("패치 제거 실패: {error}");
            }
            pause();
            result
        }
        UriAction::Settings => settings_menu(&paths, &mut settings),
    }
}

#[cfg(not(windows))]
pub fn run() -> Result<(), CliError> {
    Err(CliError::UnsupportedPlatform)
}
