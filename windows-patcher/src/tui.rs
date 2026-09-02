use std::fs;
use std::io;
use std::path::{Path, PathBuf};
#[cfg(windows)]
use std::process::Command;
use std::time::{Duration, Instant};

use crossterm::event::{
    self, DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture,
    Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use crossterm::execute;
use ratatui::layout::{Alignment, Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{
    Block, Borders, Cell, Gauge, HighlightSpacing, List, ListItem, ListState, Paragraph, Row,
    Table, TableState, Wrap,
};
use ratatui::{DefaultTerminal, Frame};

use crate::game::GameRoute;
use crate::install::{ApplyPhase, RemoveIssueSummary};
use crate::logging;
use crate::service::{
    InstallOutcome, InstallProgress, PatchFileInfo, PatcherPaths, ServiceError,
    install_latest_compatible_with_progress, install_roots, installed_patch_info,
    remove_installed_patch, reset_patch_state,
};
use crate::settings::AppSettings;
use crate::uri::{UriAction, UriRequest};

const MIN_WIDTH: u16 = 72;
const MIN_HEIGHT: u16 = 26;
const MAIN_ITEMS: [&str; 4] = [
    "Steam 패치 설치 / 업데이트",
    "Steam 패치 제거",
    "Steam 설정",
    "종료",
];
const RESULT_ITEMS: [&str; 2] = ["메인 메뉴", "종료"];
const PATCH_STATE_RESET_ITEMS: [&str; 2] = ["패치 상태 초기화 후 다시 설치", "취소하고 메인 메뉴"];
const REMOVE_STATE_RESET_ITEMS: [&str; 2] = ["패치 기록만 초기화", "취소하고 메인 메뉴"];
const SETTINGS_ITEM_COUNT: usize = 6;

#[derive(Debug, Clone, Copy)]
pub struct RemoteEndpoints {
    pub patch_release_index: &'static str,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Screen {
    Main,
    Settings,
    PathInput(PathKind),
    Operation,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PathKind {
    Steam,
    LocalLow,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum OperationKind {
    SteamInstall,
    SteamRemove,
}

impl OperationKind {
    fn title(self) -> &'static str {
        match self {
            Self::SteamInstall => "Steam 패치 설치 / 업데이트",
            Self::SteamRemove => "Steam 패치 제거",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum SteamInstallError {
    ExistingPatchChanged(usize),
    ExistingPatchUnsafe(RemoveIssueSummary),
    Other(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum SteamRemoveError {
    ExistingPatchUnsafe(RemoveIssueSummary),
    Other(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum OperationPhase {
    Pending,
    Running,
    NeedsPatchStateReset { details: String },
    NeedsRemoveStateReset { summary: RemoveIssueSummary },
    Finished { success: bool, message: String },
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct OperationState {
    kind: OperationKind,
    route: GameRoute,
    protocol_request: bool,
    phase: OperationPhase,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct InstallUiProgress {
    patch_version: Option<String>,
    files: Vec<PatchFileInfo>,
    download_current: u64,
    download_total: u64,
    apply_current: u64,
    apply_total: u64,
    status: String,
}

impl Default for InstallUiProgress {
    fn default() -> Self {
        Self {
            patch_version: None,
            files: Vec::new(),
            download_current: 0,
            download_total: 0,
            apply_current: 0,
            apply_total: 0,
            status: "호환 패치를 확인하는 중입니다...".into(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum HitTarget {
    Main(usize),
    Settings(usize),
    Result(usize),
    PatchStateReset(usize),
    RemoveStateReset(usize),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct HitRegion {
    rect: Rect,
    target: HitTarget,
}

#[derive(Debug)]
struct App {
    paths: PatcherPaths,
    settings: AppSettings,
    installed_exe: PathBuf,
    endpoints: RemoteEndpoints,
    screen: Screen,
    main_selected: usize,
    settings_selected: usize,
    result_selected: usize,
    input: String,
    operation: Option<OperationState>,
    install_progress: InstallUiProgress,
    notice: Option<String>,
    hit_regions: Vec<HitRegion>,
    should_quit: bool,
}

impl App {
    fn new(
        paths: PatcherPaths,
        settings: AppSettings,
        installed_exe: PathBuf,
        initial_request: UriRequest,
        startup_notice: Option<String>,
        endpoints: RemoteEndpoints,
    ) -> Self {
        let mut app = Self {
            paths,
            settings,
            installed_exe,
            endpoints,
            screen: Screen::Main,
            main_selected: 0,
            settings_selected: 0,
            result_selected: 0,
            input: String::new(),
            operation: None,
            install_progress: InstallUiProgress::default(),
            notice: startup_notice,
            hit_regions: Vec::new(),
            should_quit: false,
        };
        if let Some(route) = initial_request.route {
            app.settings.auto_detect_route_missing(route);
        }
        match initial_request.action {
            UriAction::Menu => {}
            UriAction::Install => {
                app.start_operation(OperationKind::SteamInstall, true, initial_request.route)
            }
            UriAction::Remove => {
                app.start_operation(OperationKind::SteamRemove, true, initial_request.route)
            }
            UriAction::Settings => {
                if let Some(route) = initial_request.route {
                    app.settings.set_selected_route(route);
                }
                app.screen = Screen::Settings;
                app.notice = Some("웹사이트에서 프로그램 설정을 요청했습니다.".into());
            }
        }
        app
    }

    fn start_operation(
        &mut self,
        kind: OperationKind,
        protocol_request: bool,
        route_override: Option<GameRoute>,
    ) {
        if kind == OperationKind::SteamInstall {
            self.install_progress = InstallUiProgress::default();
        }
        let route = route_override.unwrap_or_else(|| self.settings.selected_route());
        logging::info(format!(
            "operation requested: kind={kind:?} route={}",
            route.as_str()
        ));
        self.operation = Some(OperationState {
            kind,
            route,
            protocol_request,
            phase: OperationPhase::Pending,
        });
        self.result_selected = 0;
        self.screen = Screen::Operation;
        self.notice = None;
    }

    fn operation_pending(&self) -> bool {
        self.operation
            .as_ref()
            .is_some_and(|state| matches!(&state.phase, OperationPhase::Pending))
    }

    fn mark_operation_running(&mut self) {
        if let Some(operation) = &mut self.operation
            && matches!(&operation.phase, OperationPhase::Pending)
        {
            operation.phase = OperationPhase::Running;
        }
    }

    fn update_install_progress(&mut self, event: InstallProgress) {
        match event {
            InstallProgress::Resolving => {
                self.install_progress.status = "호환 패치를 확인하는 중입니다...".into();
            }
            InstallProgress::Selected {
                patch_version,
                files,
                download_total,
                install_total,
            } => {
                self.install_progress.patch_version = Some(patch_version);
                self.install_progress.files = files;
                self.install_progress.download_current = 0;
                self.install_progress.download_total = download_total;
                self.install_progress.apply_current = 0;
                self.install_progress.apply_total = install_total;
                self.install_progress.status = "패치 정보를 확인했습니다.".into();
            }
            InstallProgress::Downloading {
                file_index,
                file_count,
                file_name,
                current,
                total,
            } => {
                self.install_progress.download_current = current;
                self.install_progress.download_total = total;
                self.install_progress.status =
                    format!("다운로드 중 ({file_index}/{file_count}): {file_name}");
            }
            InstallProgress::Extracting {
                file_index,
                file_count,
                file_name,
                current,
                total,
            } => {
                self.install_progress.status = format!(
                    "압축 해제/검증 중 ({file_index}/{file_count}): {file_name} [{} / {}]",
                    format_bytes(current),
                    format_bytes(total)
                );
            }
            InstallProgress::RemovingExisting { patch_version } => {
                self.install_progress.status =
                    format!("기존 패치 {patch_version}을 안전하게 제거하는 중입니다...");
            }
            InstallProgress::Applying {
                file_index,
                file_count,
                path,
                phase,
                current,
                total,
            } => {
                self.install_progress.apply_current = current;
                self.install_progress.apply_total = total;
                let action = match phase {
                    ApplyPhase::VerifyingStage => "적용 전 검증",
                    ApplyPhase::BackingUp => "복원 원본 확인",
                    ApplyPhase::Copying => "패치 적용",
                    ApplyPhase::VerifyingInstalled => "적용 후 검증",
                };
                self.install_progress.status =
                    format!("{action} 중 ({file_index}/{file_count}): {path}");
            }
        }
    }

    fn perform_remove(&self, route: GameRoute) -> Result<String, SteamRemoveError> {
        let game = self
            .settings
            .installation_for(route)
            .map_err(|error| SteamRemoveError::Other(error.to_string()))?;
        let roots = install_roots(&game);
        match remove_installed_patch(&self.paths, &roots, game.route) {
            Ok(None) => Ok("설치된 패치 기록이 없습니다.".into()),
            Ok(Some(report)) => Ok(format!(
                "패치를 제거했습니다. 삭제 {}개, 복구 {}개",
                report.removed, report.restored
            )),
            Err(ServiceError::ExistingPatchUnsafe(summary)) => {
                Err(SteamRemoveError::ExistingPatchUnsafe(summary))
            }
            Err(error) => Err(SteamRemoveError::Other(error.to_string())),
        }
    }

    fn path_changes_allowed(&mut self) -> bool {
        let state = self.paths.route_state(self.settings.selected_route());
        match installed_patch_info(&state.ownership_path) {
            Ok(Some(_)) => {
                self.notice = Some(
                    "패치가 설치된 상태에서는 게임 경로를 변경할 수 없습니다. 먼저 패치를 제거하세요."
                        .into(),
                );
                false
            }
            Ok(None) => true,
            Err(error) => {
                self.notice = Some(format!("설치 상태를 확인하지 못했습니다: {error}"));
                false
            }
        }
    }

    fn select_next_route(&mut self) {
        let old = self.settings.clone();
        self.settings.select_next_route();
        self.settings.auto_detect_selected_missing();
        if let Err(error) = self.settings.save(&self.paths.settings_path) {
            self.settings = old;
            self.notice = Some(format!("route 설정을 저장하지 못했습니다: {error}"));
            return;
        }
        self.settings_selected = 0;
        self.notice = Some(format!(
            "Steam 지역을 {}으로 변경했습니다.",
            self.settings.selected_route().display_name()
        ));
    }

    fn begin_path_input(&mut self, kind: PathKind) {
        if !self.path_changes_allowed() {
            return;
        }
        self.input.clear();
        self.notice = None;
        self.screen = Screen::PathInput(kind);
    }

    fn save_path_input(&mut self, kind: PathKind) {
        let path = clean_input_path(&self.input);
        if self.input.trim().is_empty() {
            self.notice = Some("경로를 입력하세요.".into());
            return;
        }
        let old = self.settings.clone();
        let result = match kind {
            PathKind::Steam => self.settings.set_steam_game_root(&path),
            PathKind::LocalLow => self.settings.set_locallow_root(&path),
        };
        if let Err(error) = result {
            self.settings = old;
            self.notice = Some(format!("유효한 게임 경로가 아닙니다: {error}"));
            return;
        }
        if let Err(error) = self.settings.save(&self.paths.settings_path) {
            self.settings = old;
            self.notice = Some(format!("설정을 저장하지 못했습니다: {error}"));
            return;
        }
        self.notice = Some("게임 경로를 저장했습니다.".into());
        self.screen = Screen::Settings;
        self.input.clear();
    }

    fn redetect_paths(&mut self) {
        if !self.path_changes_allowed() {
            return;
        }
        let old = self.settings.clone();
        if let Err(error) = self.settings.redetect_all() {
            self.notice = Some(format!("자동 찾기에 실패했습니다: {error}"));
            return;
        }
        if let Err(error) = self.settings.save(&self.paths.settings_path) {
            self.settings = old;
            self.notice = Some(format!("설정을 저장하지 못했습니다: {error}"));
            return;
        }
        self.notice = Some("설치 경로와 리소스 경로를 다시 찾았습니다.".into());
    }

    fn open_logs_folder(&mut self) {
        if let Err(error) = fs::create_dir_all(&self.paths.logs_root) {
            logging::error(format!("failed to create log directory: {error}"));
            self.notice = Some(format!("로그 폴더를 만들지 못했습니다: {error}"));
            return;
        }
        #[cfg(windows)]
        let result = Command::new("explorer.exe")
            .arg(&self.paths.logs_root)
            .spawn();
        #[cfg(not(windows))]
        let result: io::Result<std::process::Child> = Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "로그 폴더 열기는 Windows에서만 지원됩니다",
        ));
        match result {
            Ok(_) => {
                logging::info(format!(
                    "opened log directory: {}",
                    self.paths.logs_root.display()
                ));
                self.notice = Some("로그 폴더를 열었습니다.".into());
            }
            Err(error) => {
                logging::error(format!("failed to open log directory: {error}"));
                self.notice = Some(format!("로그 폴더를 열지 못했습니다: {error}"));
            }
        }
    }

    fn activate_main(&mut self) {
        match self.main_selected {
            0 => self.start_operation(OperationKind::SteamInstall, false, None),
            1 => self.start_operation(OperationKind::SteamRemove, false, None),
            2 => {
                self.screen = Screen::Settings;
                self.notice = None;
            }
            3 => self.should_quit = true,
            _ => {}
        }
    }

    fn activate_settings(&mut self) {
        match self.settings_selected {
            0 => self.select_next_route(),
            1 => self.begin_path_input(PathKind::Steam),
            2 => self.begin_path_input(PathKind::LocalLow),
            3 => self.redetect_paths(),
            4 => self.open_logs_folder(),
            5 => {
                self.screen = Screen::Main;
                self.notice = None;
            }
            _ => {}
        }
    }

    fn activate_result(&mut self) {
        match self.result_selected {
            0 => {
                self.operation = None;
                self.screen = Screen::Main;
                self.notice = None;
            }
            1 => self.should_quit = true,
            _ => {}
        }
    }

    fn activate_patch_state_reset_result(&mut self) {
        match self.result_selected {
            0 => {
                let Some(route) = self.operation.as_ref().map(|operation| operation.route) else {
                    return;
                };
                match reset_patch_state(&self.paths, route) {
                    Ok(_) => {
                        logging::warn(format!(
                            "Steam install state reset accepted: route={}",
                            route.as_str()
                        ));
                        self.install_progress = InstallUiProgress::default();
                        self.install_progress.status =
                            "기존 패치 상태를 초기화했습니다. 다시 설치를 시작합니다...".into();
                        if let Some(operation) = &mut self.operation {
                            operation.phase = OperationPhase::Pending;
                        }
                    }
                    Err(error) => {
                        logging::error(format!(
                            "Steam install state reset failed: route={} error={error}",
                            route.as_str()
                        ));
                        if let Some(operation) = &mut self.operation {
                            operation.phase = OperationPhase::Finished {
                                success: false,
                                message: format!("패치 상태 초기화에 실패했습니다: {error}"),
                            };
                        }
                    }
                }
            }
            1 => {
                self.operation = None;
                self.screen = Screen::Main;
                self.notice = None;
            }
            _ => {}
        }
    }

    fn activate_remove_state_reset_result(&mut self) {
        match self.result_selected {
            0 => {
                let Some(route) = self.operation.as_ref().map(|operation| operation.route) else {
                    return;
                };
                match reset_patch_state(&self.paths, route) {
                    Ok(_) => {
                        logging::warn(format!(
                            "Steam remove state reset accepted: route={}",
                            route.as_str()
                        ));
                        if let Some(operation) = &mut self.operation {
                            operation.phase = OperationPhase::Finished {
                                success: true,
                                message: "패치 기록을 초기화했습니다. 게임 파일은 변경하지 않았습니다. 필요한 경우 Steam 파일 무결성 검사를 완료하세요.".into(),
                            };
                        }
                    }
                    Err(error) => {
                        logging::error(format!(
                            "Steam remove state reset failed: route={} error={error}",
                            route.as_str()
                        ));
                        if let Some(operation) = &mut self.operation {
                            operation.phase = OperationPhase::Finished {
                                success: false,
                                message: format!("패치 기록 초기화에 실패했습니다: {error}"),
                            };
                        }
                    }
                }
            }
            1 => {
                self.operation = None;
                self.screen = Screen::Main;
                self.notice = None;
            }
            _ => {}
        }
    }

    fn handle_key(&mut self, key: KeyEvent) {
        if !matches!(key.kind, KeyEventKind::Press | KeyEventKind::Repeat) {
            return;
        }
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
            self.should_quit = true;
            return;
        }
        match self.screen {
            Screen::Main => self.handle_main_key(key.code),
            Screen::Settings => self.handle_settings_key(key.code),
            Screen::PathInput(kind) => self.handle_input_key(kind, key.code),
            Screen::Operation => self.handle_operation_key(key.code),
        }
    }

    fn handle_main_key(&mut self, code: KeyCode) {
        match code {
            KeyCode::Up => self.main_selected = previous(self.main_selected, MAIN_ITEMS.len()),
            KeyCode::Down => self.main_selected = next(self.main_selected, MAIN_ITEMS.len()),
            KeyCode::Enter => self.activate_main(),
            KeyCode::Esc | KeyCode::Char('q') => self.should_quit = true,
            _ => {}
        }
    }

    fn handle_settings_key(&mut self, code: KeyCode) {
        match code {
            KeyCode::Up => {
                self.settings_selected = previous(self.settings_selected, SETTINGS_ITEM_COUNT)
            }
            KeyCode::Down => {
                self.settings_selected = next(self.settings_selected, SETTINGS_ITEM_COUNT)
            }
            KeyCode::Enter => self.activate_settings(),
            KeyCode::Esc => {
                self.screen = Screen::Main;
                self.notice = None;
            }
            _ => {}
        }
    }

    fn handle_input_key(&mut self, kind: PathKind, code: KeyCode) {
        match code {
            KeyCode::Enter => self.save_path_input(kind),
            KeyCode::Esc => {
                self.screen = Screen::Settings;
                self.input.clear();
                self.notice = None;
            }
            KeyCode::Backspace => {
                self.input.pop();
            }
            KeyCode::Char(character) => self.input.push(character),
            _ => {}
        }
    }

    fn operation_selection_len(&self) -> usize {
        match self.operation.as_ref().map(|operation| &operation.phase) {
            Some(OperationPhase::NeedsPatchStateReset { .. }) => PATCH_STATE_RESET_ITEMS.len(),
            Some(OperationPhase::NeedsRemoveStateReset { .. }) => REMOVE_STATE_RESET_ITEMS.len(),
            Some(OperationPhase::Finished { .. }) => RESULT_ITEMS.len(),
            _ => 0,
        }
    }

    fn handle_operation_key(&mut self, code: KeyCode) {
        let phase = self
            .operation
            .as_ref()
            .map(|operation| operation.phase.clone());
        match phase {
            Some(OperationPhase::NeedsPatchStateReset { .. }) => match code {
                KeyCode::Up => {
                    self.result_selected =
                        previous(self.result_selected, PATCH_STATE_RESET_ITEMS.len())
                }
                KeyCode::Down => {
                    self.result_selected = next(self.result_selected, PATCH_STATE_RESET_ITEMS.len())
                }
                KeyCode::Enter => self.activate_patch_state_reset_result(),
                KeyCode::Esc => {
                    self.operation = None;
                    self.screen = Screen::Main;
                }
                _ => {}
            },
            Some(OperationPhase::NeedsRemoveStateReset { .. }) => match code {
                KeyCode::Up => {
                    self.result_selected =
                        previous(self.result_selected, REMOVE_STATE_RESET_ITEMS.len())
                }
                KeyCode::Down => {
                    self.result_selected =
                        next(self.result_selected, REMOVE_STATE_RESET_ITEMS.len())
                }
                KeyCode::Enter => self.activate_remove_state_reset_result(),
                KeyCode::Esc => {
                    self.operation = None;
                    self.screen = Screen::Main;
                }
                _ => {}
            },
            Some(OperationPhase::Finished { .. }) => match code {
                KeyCode::Up => {
                    self.result_selected = previous(self.result_selected, RESULT_ITEMS.len())
                }
                KeyCode::Down => {
                    self.result_selected = next(self.result_selected, RESULT_ITEMS.len())
                }
                KeyCode::Enter => self.activate_result(),
                KeyCode::Esc => {
                    self.operation = None;
                    self.screen = Screen::Main;
                }
                KeyCode::Char('q') => self.should_quit = true,
                _ => {}
            },
            _ => {}
        }
    }

    fn handle_mouse(&mut self, mouse: MouseEvent) {
        match mouse.kind {
            MouseEventKind::Down(MouseButton::Left) => {
                let target = self
                    .hit_regions
                    .iter()
                    .find(|region| contains(region.rect, mouse.column, mouse.row))
                    .map(|region| region.target);
                match target {
                    Some(HitTarget::Main(index)) => {
                        self.main_selected = index;
                        self.activate_main();
                    }
                    Some(HitTarget::Settings(index)) => {
                        self.settings_selected = index;
                        self.activate_settings();
                    }
                    Some(HitTarget::Result(index)) => {
                        self.result_selected = index;
                        self.activate_result();
                    }
                    Some(HitTarget::PatchStateReset(index)) => {
                        self.result_selected = index;
                        self.activate_patch_state_reset_result();
                    }
                    Some(HitTarget::RemoveStateReset(index)) => {
                        self.result_selected = index;
                        self.activate_remove_state_reset_result();
                    }
                    None => {}
                }
            }
            MouseEventKind::ScrollUp => match self.screen {
                Screen::Main => self.main_selected = previous(self.main_selected, MAIN_ITEMS.len()),
                Screen::Settings => {
                    self.settings_selected = previous(self.settings_selected, SETTINGS_ITEM_COUNT)
                }
                Screen::Operation => {
                    let length = self.operation_selection_len();
                    self.result_selected = previous(self.result_selected, length);
                }
                Screen::PathInput(_) => {}
            },
            MouseEventKind::ScrollDown => match self.screen {
                Screen::Main => self.main_selected = next(self.main_selected, MAIN_ITEMS.len()),
                Screen::Settings => {
                    self.settings_selected = next(self.settings_selected, SETTINGS_ITEM_COUNT)
                }
                Screen::Operation => {
                    let length = self.operation_selection_len();
                    self.result_selected = next(self.result_selected, length);
                }
                Screen::PathInput(_) => {}
            },
            _ => {}
        }
    }

    fn handle_event(&mut self, event: Event) {
        match event {
            Event::Key(key) => self.handle_key(key),
            Event::Mouse(mouse) => self.handle_mouse(mouse),
            Event::Paste(text) => {
                if matches!(self.screen, Screen::PathInput(_)) {
                    let pasted = text.replace(['\r', '\n'], "");
                    self.input.push_str(&pasted);
                }
            }
            Event::Resize(_, _) | Event::FocusGained | Event::FocusLost => {}
        }
    }
}

struct TerminalExtras;

impl TerminalExtras {
    fn enable() -> io::Result<Self> {
        execute!(io::stdout(), EnableMouseCapture, EnableBracketedPaste)?;
        Ok(Self)
    }
}

impl Drop for TerminalExtras {
    fn drop(&mut self) {
        let _ = execute!(io::stdout(), DisableBracketedPaste, DisableMouseCapture);
    }
}

pub fn run(
    paths: PatcherPaths,
    settings: AppSettings,
    installed_exe: PathBuf,
    initial_request: UriRequest,
    startup_notice: Option<String>,
    endpoints: RemoteEndpoints,
) -> io::Result<()> {
    ratatui::run(|terminal| {
        let _extras = TerminalExtras::enable()?;
        let mut app = App::new(
            paths,
            settings,
            installed_exe,
            initial_request,
            startup_notice,
            endpoints,
        );
        run_loop(terminal, &mut app)
    })
}

fn run_loop(terminal: &mut DefaultTerminal, app: &mut App) -> io::Result<()> {
    while !app.should_quit {
        terminal.draw(|frame| render(frame, app))?;
        if app.operation_pending() {
            app.mark_operation_running();
            terminal.draw(|frame| render(frame, app))?;
            execute_operation(terminal, app);
            continue;
        }
        app.handle_event(event::read()?);
    }
    Ok(())
}

fn execute_operation(terminal: &mut DefaultTerminal, app: &mut App) {
    let Some((kind, route)) = app
        .operation
        .as_ref()
        .map(|state| (state.kind, state.route))
    else {
        return;
    };

    if kind == OperationKind::SteamInstall {
        let phase = match perform_install(terminal, app, route) {
            Ok(message) => OperationPhase::Finished {
                success: true,
                message,
            },
            Err(SteamInstallError::ExistingPatchChanged(changed_files)) => {
                app.result_selected = 0;
                OperationPhase::NeedsPatchStateReset {
                    details: format!("관리 중인 파일 {changed_files}개가 설치 기록과 다릅니다."),
                }
            }
            Err(SteamInstallError::ExistingPatchUnsafe(summary)) => {
                app.result_selected = 0;
                OperationPhase::NeedsPatchStateReset {
                    details: format!("기존 패치를 안전하게 정리할 수 없습니다. {summary}"),
                }
            }
            Err(SteamInstallError::Other(message)) => OperationPhase::Finished {
                success: false,
                message,
            },
        };
        if let Some(operation) = &mut app.operation {
            operation.phase = phase;
        }
        return;
    }

    if kind == OperationKind::SteamRemove {
        let phase = match app.perform_remove(route) {
            Ok(message) => OperationPhase::Finished {
                success: true,
                message,
            },
            Err(SteamRemoveError::ExistingPatchUnsafe(summary)) => {
                app.result_selected = 0;
                OperationPhase::NeedsRemoveStateReset { summary }
            }
            Err(SteamRemoveError::Other(message)) => OperationPhase::Finished {
                success: false,
                message,
            },
        };
        if let Some(operation) = &mut app.operation {
            operation.phase = phase;
        }
    }
}

fn perform_install(
    terminal: &mut DefaultTerminal,
    app: &mut App,
    route: GameRoute,
) -> Result<String, SteamInstallError> {
    let paths = app.paths.clone();
    let settings = app.settings.clone();
    let release_index_url = app.endpoints.patch_release_index;
    let game = settings
        .installation_for(route)
        .map_err(|error| SteamInstallError::Other(error.to_string()))?;
    let mut last_draw = Instant::now();
    let outcome =
        install_latest_compatible_with_progress(release_index_url, &paths, &game, |event| {
            let force_redraw = progress_requires_immediate_redraw(&event);
            app.update_install_progress(event);
            if force_redraw || last_draw.elapsed() >= Duration::from_millis(50) {
                let _ = terminal.draw(|frame| render(frame, app));
                last_draw = Instant::now();
            }
        })
        .map_err(|error| match error {
            ServiceError::ExistingPatchChanged(changed_files) => {
                SteamInstallError::ExistingPatchChanged(changed_files)
            }
            ServiceError::ExistingPatchUnsafe(summary) => {
                SteamInstallError::ExistingPatchUnsafe(summary)
            }
            other => SteamInstallError::Other(other.to_string()),
        })?;
    match outcome {
        InstallOutcome::AlreadyInstalled(info) => Ok(format!(
            "{} 패치가 이미 설치되어 있습니다.",
            info.patch_version
        )),
        InstallOutcome::Installed(summary) => {
            let patch_version = app
                .install_progress
                .patch_version
                .as_deref()
                .unwrap_or("선택된 패치");
            Ok(format!(
                "{patch_version} 설치가 완료되었습니다. 새 파일 {}개, 교체 파일 {}개",
                summary.created, summary.modified
            ))
        }
    }
}

fn progress_requires_immediate_redraw(event: &InstallProgress) -> bool {
    match event {
        InstallProgress::Resolving
        | InstallProgress::Selected { .. }
        | InstallProgress::RemovingExisting { .. } => true,
        InstallProgress::Downloading { current, total, .. }
        | InstallProgress::Extracting { current, total, .. } => *current == 0 || current >= total,
        InstallProgress::Applying {
            phase,
            current,
            total,
            ..
        } => !matches!(phase, ApplyPhase::Copying) || *current == 0 || current >= total,
    }
}

fn render(frame: &mut Frame<'_>, app: &mut App) {
    app.hit_regions.clear();
    let area = frame.area();
    if area.width < MIN_WIDTH || area.height < MIN_HEIGHT {
        let text = format!(
            "WindowsPatcher v{}\n\n터미널 창이 너무 작습니다.\n최소 {} x {} 이상으로 늘려주세요.\n\n현재 크기: {} x {}",
            env!("CARGO_PKG_VERSION"),
            MIN_WIDTH,
            MIN_HEIGHT,
            area.width,
            area.height
        );
        frame.render_widget(
            Paragraph::new(text)
                .alignment(Alignment::Center)
                .block(Block::default().borders(Borders::ALL).title(" 화면 크기 ")),
            area,
        );
        return;
    }

    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(9),
            Constraint::Min(8),
            Constraint::Length(3),
        ])
        .split(area);
    render_status(frame, app, chunks[0]);
    match app.screen {
        Screen::Main => render_main(frame, app, chunks[1]),
        Screen::Settings => render_settings(frame, app, chunks[1]),
        Screen::PathInput(kind) => render_path_input(frame, app, chunks[1], kind),
        Screen::Operation => render_operation(frame, app, chunks[1]),
    }
    render_footer(frame, app, chunks[2]);
}

fn render_status(frame: &mut Frame<'_>, app: &App, area: Rect) {
    let game_version = match app.settings.installation() {
        Ok(game) => game.catalog.version,
        Err(error) => format!("감지 실패 ({error})"),
    };
    let route = app.settings.selected_route();
    let state = app.paths.route_state(route);
    let installed = match installed_patch_info(&state.ownership_path) {
        Ok(Some(info)) => info.patch_version,
        Ok(None) => "미설치".into(),
        Err(error) => format!("상태 확인 실패 ({error})"),
    };
    let title = format!(" WindowsPatcher v{} ", env!("CARGO_PKG_VERSION"));
    let block = Block::default().borders(Borders::ALL).title(title);
    let inner = block.inner(area);
    frame.render_widget(block, area);

    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(1), Constraint::Length(6)])
        .split(inner);

    frame.render_widget(
        Paragraph::new(status_text_line(
            "Patcher 경로",
            app.installed_exe.display().to_string(),
        )),
        rows[0],
    );

    let steam = Text::from(vec![
        Line::from(Span::styled(
            "Steam",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
        status_text_line("지역", route.display_name().to_string()),
        status_text_line("설치 경로", display_steam_route_path(&app.settings)),
        status_text_line("리소스 경로", display_path(app.settings.locallow_root())),
        status_text_line("현재 버전", game_version),
        status_text_line("현재 패치", installed),
    ]);
    frame.render_widget(Paragraph::new(steam), rows[1]);
}

fn status_text_line(label: &'static str, value: String) -> Line<'static> {
    Line::from(vec![
        Span::styled(label, Style::default().add_modifier(Modifier::BOLD)),
        Span::raw(" : "),
        Span::raw(value),
    ])
}

fn render_main(frame: &mut Frame<'_>, app: &mut App, area: Rect) {
    let block = Block::default().borders(Borders::ALL).title(" 메인 메뉴 ");
    let inner = block.inner(area);
    let items = MAIN_ITEMS.map(ListItem::new);
    let list = List::new(items)
        .block(block)
        .highlight_symbol("▶ ")
        .highlight_style(Style::default().add_modifier(Modifier::BOLD | Modifier::REVERSED));
    let mut state = ListState::default();
    state.select(Some(app.main_selected));
    frame.render_stateful_widget(list, area, &mut state);
    add_list_hits(
        &mut app.hit_regions,
        inner,
        MAIN_ITEMS.len(),
        HitTarget::Main,
    );
}

fn render_settings(frame: &mut Frame<'_>, app: &mut App, area: Rect) {
    let rows = [
        Row::new(vec![
            Cell::from("Steam 지역"),
            Cell::from(format!(
                "[{}]",
                app.settings.selected_route().display_name()
            )),
        ]),
        Row::new(vec![
            Cell::from("설치 경로"),
            Cell::from(format!("[{}]", display_steam_route_path(&app.settings))),
        ]),
        Row::new(vec![
            Cell::from("리소스 경로"),
            Cell::from(format!("[{}]", display_path(app.settings.locallow_root()))),
        ]),
        Row::new(vec![Cell::from("게임 경로 자동 찾기"), Cell::from("")]),
        Row::new(vec![Cell::from("로그 폴더 열기"), Cell::from("")]),
        Row::new(vec![Cell::from("메인 메뉴"), Cell::from("")]),
    ];
    let block = Block::default()
        .borders(Borders::ALL)
        .title(" 프로그램 설정 ");
    let inner = block.inner(area);
    let table = Table::new(rows, [Constraint::Length(26), Constraint::Min(1)])
        .block(block)
        .highlight_symbol("▶ ")
        .highlight_spacing(HighlightSpacing::Always)
        .row_highlight_style(Style::default().add_modifier(Modifier::BOLD | Modifier::REVERSED));
    let mut state = TableState::default();
    state.select(Some(app.settings_selected));
    frame.render_stateful_widget(table, area, &mut state);
    add_list_hits(
        &mut app.hit_regions,
        inner,
        SETTINGS_ITEM_COUNT,
        HitTarget::Settings,
    );
}

fn render_path_input(frame: &mut Frame<'_>, app: &App, area: Rect, kind: PathKind) {
    let route = app.settings.selected_route();
    let (title, help, current) = match kind {
        PathKind::Steam => (
            " 설치 경로 입력 ",
            format!(
                "Steam의 'Astral Party', '{}', 또는 '{}' 경로를 입력하세요.",
                route.executable_dir(),
                route.data_dir()
            ),
            display_steam_route_path(&app.settings),
        ),
        PathKind::LocalLow => (
            " 리소스 경로 입력 ",
            format!(
                "Astral Party 리소스 폴더 '{}'의 경로를 입력하세요.",
                route.locallow_dir()
            ),
            display_path(app.settings.locallow_root()),
        ),
    };
    let text = Text::from(vec![
        Line::from(help),
        Line::from(format!("현재 경로: {current}")),
        Line::from(""),
        Line::from(vec![
            Span::styled("> ", Style::default().add_modifier(Modifier::BOLD)),
            Span::raw(&app.input),
            Span::styled("█", Style::default().fg(Color::Cyan)),
        ]),
        Line::from(""),
        Line::from("Enter 저장 · Esc 취소 · 붙여넣기 지원"),
    ]);
    frame.render_widget(
        Paragraph::new(text)
            .block(Block::default().borders(Borders::ALL).title(title))
            .wrap(Wrap { trim: false }),
        area,
    );
}

fn render_operation(frame: &mut Frame<'_>, app: &mut App, area: Rect) {
    let Some(operation) = app.operation.clone() else {
        return;
    };
    let title = format!(" {} ", operation.kind.title());
    let pending = matches!(&operation.phase, OperationPhase::Pending);
    match operation.phase {
        OperationPhase::Pending | OperationPhase::Running => {
            match operation.kind {
                OperationKind::SteamInstall => {
                    render_install_running(frame, app, area, &operation, pending);
                    return;
                }
                OperationKind::SteamRemove => {}
            }
            let status = if pending {
                "현재 상태 정보를 표시했습니다. 작업을 시작합니다..."
            } else {
                "설치 기록을 검증하고 Steam 패치를 제거하는 중입니다..."
            };
            let text = Text::from(vec![
                Line::from(vec![
                    Span::styled(
                        "Steam 지역: ",
                        Style::default().add_modifier(Modifier::BOLD),
                    ),
                    Span::raw(operation.route.display_name()),
                ]),
                Line::from(""),
                Line::from(Span::styled(status, Style::default().fg(Color::Yellow))),
                Line::from(""),
                Line::from("작업이 끝나면 이 화면에 결과가 표시됩니다."),
            ]);
            frame.render_widget(
                Paragraph::new(text)
                    .block(Block::default().borders(Borders::ALL).title(title))
                    .wrap(Wrap { trim: false }),
                area,
            );
        }
        OperationPhase::NeedsPatchStateReset { details } => {
            let chunks = Layout::default()
                .direction(Direction::Vertical)
                .constraints([Constraint::Min(12), Constraint::Length(4)])
                .split(area);
            let text = Text::from(vec![
                Line::from(Span::styled(
                    "기존 패치 상태를 자동으로 복구할 수 없습니다.",
                    Style::default()
                        .fg(Color::Yellow)
                        .add_modifier(Modifier::BOLD),
                )),
                Line::from(""),
                Line::from(details),
                Line::from(
                    "Steam 파일 무결성 검사, 게임 재다운로드/업데이트 등으로 생길 수 있습니다.",
                ),
                Line::from(""),
                Line::from(Span::styled(
                    "패치 상태 초기화는 게임 파일을 수정하거나 삭제하지 않습니다.",
                    Style::default().add_modifier(Modifier::BOLD),
                )),
                Line::from(
                    "WindowsPatcher의 기존 installed/backup/staging 기록만 폐기하고, 다음 설치에서 릴리즈 원본과 현재 게임 파일을 다시 검증합니다.",
                ),
                Line::from(""),
                Line::from(
                    "다른 모드나 수동 수정이 남아 있으면 원본 검증에 실패하여 설치가 중단됩니다.",
                ),
                Line::from("Steam 파일이 정상 상태인지 확인한 경우에만 초기화를 선택하세요."),
            ]);
            frame.render_widget(
                Paragraph::new(text)
                    .block(Block::default().borders(Borders::ALL).title(title))
                    .wrap(Wrap { trim: false }),
                chunks[0],
            );
            let result_block = Block::default().borders(Borders::ALL).title(" 복구 선택 ");
            let inner = result_block.inner(chunks[1]);
            let list = List::new(PATCH_STATE_RESET_ITEMS.map(ListItem::new))
                .block(result_block)
                .highlight_symbol("▶ ")
                .highlight_style(
                    Style::default().add_modifier(Modifier::BOLD | Modifier::REVERSED),
                );
            let mut state = ListState::default();
            state.select(Some(app.result_selected));
            frame.render_stateful_widget(list, chunks[1], &mut state);
            add_list_hits(
                &mut app.hit_regions,
                inner,
                PATCH_STATE_RESET_ITEMS.len(),
                HitTarget::PatchStateReset,
            );
        }
        OperationPhase::NeedsRemoveStateReset { summary } => {
            let chunks = Layout::default()
                .direction(Direction::Vertical)
                .constraints([Constraint::Min(12), Constraint::Length(4)])
                .split(area);
            let text = Text::from(vec![
                Line::from(Span::styled(
                    "패치를 안전하게 제거할 수 없습니다.",
                    Style::default()
                        .fg(Color::Yellow)
                        .add_modifier(Modifier::BOLD),
                )),
                Line::from(""),
                Line::from(format!("진단: {summary}")),
                Line::from(""),
                Line::from(
                    "먼저 Steam 파일 무결성 검사를 실행하면 원본 파일이 복구되어 정상 제거가 가능할 수 있습니다.",
                ),
                Line::from(""),
                Line::from(Span::styled(
                    "패치 기록 초기화는 게임 파일을 복원하거나 삭제하지 않습니다.",
                    Style::default().add_modifier(Modifier::BOLD),
                )),
                Line::from(
                    "초기화하면 WindowsPatcher의 installed/backup/staging 기록만 삭제됩니다.",
                ),
                Line::from(
                    "게임 파일이 정상 상태임을 확인했거나 Steam 파일 무결성 검사를 완료한 경우에만 사용하세요.",
                ),
            ]);
            frame.render_widget(
                Paragraph::new(text)
                    .block(Block::default().borders(Borders::ALL).title(title))
                    .wrap(Wrap { trim: false }),
                chunks[0],
            );
            let result_block = Block::default().borders(Borders::ALL).title(" 복구 선택 ");
            let inner = result_block.inner(chunks[1]);
            let list = List::new(REMOVE_STATE_RESET_ITEMS.map(ListItem::new))
                .block(result_block)
                .highlight_symbol("▶ ")
                .highlight_style(
                    Style::default().add_modifier(Modifier::BOLD | Modifier::REVERSED),
                );
            let mut state = ListState::default();
            state.select(Some(app.result_selected));
            frame.render_stateful_widget(list, chunks[1], &mut state);
            add_list_hits(
                &mut app.hit_regions,
                inner,
                REMOVE_STATE_RESET_ITEMS.len(),
                HitTarget::RemoveStateReset,
            );
        }
        OperationPhase::Finished { success, message } => {
            let chunks = Layout::default()
                .direction(Direction::Vertical)
                .constraints([Constraint::Min(5), Constraint::Length(4)])
                .split(area);
            let status = if success { "성공" } else { "실패" };
            let color = if success { Color::Green } else { Color::Red };
            let text = Text::from(vec![
                Line::from(Span::styled(
                    status,
                    Style::default().fg(color).add_modifier(Modifier::BOLD),
                )),
                Line::from(""),
                Line::from(message),
            ]);
            frame.render_widget(
                Paragraph::new(text)
                    .block(Block::default().borders(Borders::ALL).title(title))
                    .wrap(Wrap { trim: false }),
                chunks[0],
            );

            let result_block = Block::default().borders(Borders::ALL).title(" 다음 작업 ");
            let inner = result_block.inner(chunks[1]);
            let list = List::new(RESULT_ITEMS.map(ListItem::new))
                .block(result_block)
                .highlight_symbol("▶ ")
                .highlight_style(
                    Style::default().add_modifier(Modifier::BOLD | Modifier::REVERSED),
                );
            let mut state = ListState::default();
            state.select(Some(app.result_selected));
            frame.render_stateful_widget(list, chunks[1], &mut state);
            add_list_hits(
                &mut app.hit_regions,
                inner,
                RESULT_ITEMS.len(),
                HitTarget::Result,
            );
        }
    }
}

fn render_install_running(
    frame: &mut Frame<'_>,
    app: &App,
    area: Rect,
    operation: &OperationState,
    pending: bool,
) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(6),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Min(3),
        ])
        .split(area);

    let patch_version = app
        .install_progress
        .patch_version
        .as_deref()
        .unwrap_or("확인 중...");
    let mut detail_lines = vec![
        Line::from(vec![
            Span::styled(
                "Steam 지역: ",
                Style::default().add_modifier(Modifier::BOLD),
            ),
            Span::raw(operation.route.display_name()),
        ]),
        Line::from(vec![
            Span::styled("대상 패치: ", Style::default().add_modifier(Modifier::BOLD)),
            Span::raw(patch_version),
        ]),
    ];
    if app.install_progress.files.is_empty() {
        detail_lines.push(Line::from("패치 파일 정보를 확인하는 중입니다..."));
    } else if area.width < 110 {
        detail_lines.push(Line::from(format!(
            "파일 {}개 · 다운로드 {} · 적용 {}",
            app.install_progress.files.len(),
            format_bytes(app.install_progress.download_total),
            format_bytes(app.install_progress.apply_total)
        )));
        detail_lines.push(Line::from(
            "현재 파일명은 아래 현재 작업 영역에 표시됩니다.",
        ));
    } else {
        let download_files = app
            .install_progress
            .files
            .iter()
            .map(|file| {
                format!(
                    "{} ({})",
                    file.download_name,
                    format_bytes(file.download_size)
                )
            })
            .collect::<Vec<_>>()
            .join(", ");
        let install_files = app
            .install_progress
            .files
            .iter()
            .map(|file| {
                format!(
                    "{} ({})",
                    file.install_path,
                    format_bytes(file.install_size)
                )
            })
            .collect::<Vec<_>>()
            .join(", ");
        detail_lines.push(Line::from(vec![
            Span::styled("다운로드: ", Style::default().add_modifier(Modifier::BOLD)),
            Span::raw(download_files),
        ]));
        detail_lines.push(Line::from(vec![
            Span::styled("적용 파일: ", Style::default().add_modifier(Modifier::BOLD)),
            Span::raw(install_files),
        ]));
    }
    let details = Text::from(detail_lines);
    frame.render_widget(
        Paragraph::new(details)
            .block(Block::default().borders(Borders::ALL).title(" 패치 정보 "))
            .wrap(Wrap { trim: false }),
        chunks[0],
    );

    render_progress_gauge(
        frame,
        chunks[1],
        " 다운로드 ",
        app.install_progress.download_current,
        app.install_progress.download_total,
    );
    render_progress_gauge(
        frame,
        chunks[2],
        " 적용 ",
        app.install_progress.apply_current,
        app.install_progress.apply_total,
    );

    let request = if operation.protocol_request {
        "웹사이트 프로토콜 요청"
    } else {
        "프로그램 메뉴 요청"
    };
    let status = if pending {
        "현재 상태 정보를 표시했습니다. 작업을 시작합니다..."
    } else {
        &app.install_progress.status
    };
    frame.render_widget(
        Paragraph::new(format!("{request} · {status}"))
            .block(Block::default().borders(Borders::ALL).title(" 현재 작업 "))
            .wrap(Wrap { trim: false }),
        chunks[3],
    );
}

fn render_progress_gauge(
    frame: &mut Frame<'_>,
    area: Rect,
    title: &'static str,
    current: u64,
    total: u64,
) {
    let ratio = if total == 0 {
        0.0
    } else {
        (current.min(total) as f64 / total as f64).clamp(0.0, 1.0)
    };
    let label = if total == 0 {
        "대기 중".to_owned()
    } else {
        format!(
            "{:5.1}%  {} / {}",
            ratio * 100.0,
            format_bytes(current.min(total)),
            format_bytes(total)
        )
    };
    frame.render_widget(
        Gauge::default()
            .block(Block::default().borders(Borders::ALL).title(title))
            .gauge_style(Style::default().add_modifier(Modifier::BOLD))
            .ratio(ratio)
            .label(label),
        area,
    );
}

fn render_footer(frame: &mut Frame<'_>, app: &App, area: Rect) {
    let notice = app.notice.as_deref().unwrap_or("");
    let help = match app.screen {
        Screen::Main => "↑↓ 이동 · Enter/클릭 선택 · Esc/Q 종료",
        Screen::Settings => "↑↓ 이동 · Enter/클릭 선택 · Esc 메인 메뉴",
        Screen::PathInput(_) => "경로 입력 · Enter 저장 · Esc 취소",
        Screen::Operation => match app.operation.as_ref().map(|operation| &operation.phase) {
            Some(OperationPhase::Pending | OperationPhase::Running) => "작업 진행 중",
            _ => "↑↓ 이동 · Enter/클릭 선택 · Esc 메인 메뉴",
        },
    };
    frame.render_widget(
        Paragraph::new(Text::from(vec![
            Line::from(Span::styled(
                notice,
                Style::default()
                    .fg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            )),
            Line::from(help),
        ]))
        .alignment(Alignment::Center),
        area,
    );
}

fn format_bytes(bytes: u64) -> String {
    const MIB: f64 = 1024.0 * 1024.0;
    const KIB: f64 = 1024.0;
    if bytes >= 1024 * 1024 {
        format!("{:.2} MiB", bytes as f64 / MIB)
    } else if bytes >= 1024 {
        format!("{:.1} KiB", bytes as f64 / KIB)
    } else {
        format!("{bytes} B")
    }
}

fn display_steam_route_path(settings: &AppSettings) -> String {
    settings
        .steam_game_root()
        .map(|root| root.join(settings.selected_route().executable_dir()))
        .map(|value| value.display().to_string())
        .unwrap_or_else(|| "미설정".into())
}

fn display_path(path: Option<&Path>) -> String {
    path.map(|value| value.display().to_string())
        .unwrap_or_else(|| "미설정".into())
}

fn clean_input_path(value: &str) -> PathBuf {
    let trimmed = value.trim();
    let unquoted = trimmed
        .strip_prefix('"')
        .and_then(|value| value.strip_suffix('"'))
        .unwrap_or(trimmed);
    PathBuf::from(unquoted)
}

fn previous(index: usize, length: usize) -> usize {
    if length == 0 {
        0
    } else if index == 0 {
        length - 1
    } else {
        index - 1
    }
}

fn next(index: usize, length: usize) -> usize {
    if length == 0 { 0 } else { (index + 1) % length }
}

fn add_list_hits(
    regions: &mut Vec<HitRegion>,
    inner: Rect,
    count: usize,
    target: fn(usize) -> HitTarget,
) {
    for index in 0..count.min(inner.height as usize) {
        regions.push(HitRegion {
            rect: Rect::new(inner.x, inner.y + index as u16, inner.width, 1),
            target: target(index),
        });
    }
}

fn contains(rect: Rect, column: u16, row: u16) -> bool {
    column >= rect.x
        && column < rect.x.saturating_add(rect.width)
        && row >= rect.y
        && row < rect.y.saturating_add(rect.height)
}

#[cfg(test)]
mod tests {
    use std::fs;
    use tempfile::tempdir;

    use super::*;

    fn app_request(request: UriRequest) -> App {
        let temp = tempdir().unwrap();
        App::new(
            PatcherPaths::below(temp.path().join("state")),
            AppSettings::default(),
            temp.path().join("AstralAutoPatcher.exe"),
            request,
            None,
            RemoteEndpoints {
                patch_release_index: "https://example.test/release-index.json",
            },
        )
    }

    fn app(action: UriAction) -> App {
        app_request(UriRequest {
            action,
            route: None,
        })
    }

    #[test]
    fn protocol_install_starts_on_operation_screen_before_execution() {
        let app = app(UriAction::Install);
        assert_eq!(app.screen, Screen::Operation);
        assert!(app.operation_pending());
        assert!(app.operation.as_ref().unwrap().protocol_request);
    }

    #[test]
    fn route_scoped_protocol_operation_does_not_change_selected_route() {
        let app = app_request(UriRequest {
            action: UriAction::Install,
            route: Some(GameRoute::CnSteam),
        });
        assert_eq!(app.settings.selected_route(), GameRoute::IntSteam);
        assert_eq!(app.operation.as_ref().unwrap().route, GameRoute::CnSteam);
    }

    #[test]
    fn route_scoped_settings_opens_requested_route() {
        let app = app_request(UriRequest {
            action: UriAction::Settings,
            route: Some(GameRoute::CnSteam),
        });
        assert_eq!(app.screen, Screen::Settings);
        assert_eq!(app.settings.selected_route(), GameRoute::CnSteam);
    }

    #[test]
    fn steam_install_is_the_primary_main_menu_action() {
        let mut app = app(UriAction::Menu);
        app.main_selected = 0;
        app.activate_main();
        let operation = app.operation.as_ref().unwrap();
        assert_eq!(operation.kind, OperationKind::SteamInstall);
        assert!(!operation.protocol_request);
        assert_eq!(app.screen, Screen::Operation);
    }

    #[test]
    fn patch_state_reset_confirmation_clears_metadata_and_requeues_install() {
        let mut app = app(UriAction::Menu);
        app.start_operation(
            OperationKind::SteamInstall,
            false,
            Some(GameRoute::IntSteam),
        );
        let state = app.paths.route_state(GameRoute::IntSteam);
        fs::create_dir_all(&state.backup_root).unwrap();
        fs::create_dir_all(&state.staging_root).unwrap();
        fs::create_dir_all(state.ownership_path.parent().unwrap()).unwrap();
        fs::write(&state.ownership_path, b"stale ownership").unwrap();
        fs::write(state.backup_root.join("backup.dat"), b"backup").unwrap();
        fs::write(state.staging_root.join("stage.dat"), b"stage").unwrap();
        app.operation.as_mut().unwrap().phase = OperationPhase::NeedsPatchStateReset {
            details: "4 files changed".into(),
        };
        app.result_selected = 0;

        app.activate_patch_state_reset_result();

        assert!(!state.ownership_path.exists());
        assert!(!state.backup_root.exists());
        assert!(!state.staging_root.exists());
        assert!(matches!(
            app.operation.as_ref().unwrap().phase,
            OperationPhase::Pending
        ));
        assert!(
            app.install_progress
                .status
                .contains("기존 패치 상태를 초기화했습니다")
        );
    }

    #[test]
    fn remove_state_reset_confirmation_clears_metadata_without_touching_game_files() {
        let mut app = app(UriAction::Menu);
        app.start_operation(OperationKind::SteamRemove, false, Some(GameRoute::CnSteam));
        let state = app.paths.route_state(GameRoute::CnSteam);
        fs::create_dir_all(&state.backup_root).unwrap();
        fs::create_dir_all(&state.staging_root).unwrap();
        fs::create_dir_all(state.ownership_path.parent().unwrap()).unwrap();
        fs::write(&state.ownership_path, b"stale ownership").unwrap();
        fs::write(state.backup_root.join("backup.dat"), b"backup").unwrap();
        fs::write(state.staging_root.join("stage.dat"), b"stage").unwrap();
        let game_file = app.paths.state_root.join("outside-route-state/game.dat");
        fs::create_dir_all(game_file.parent().unwrap()).unwrap();
        fs::write(&game_file, b"patched-game-file").unwrap();
        app.operation.as_mut().unwrap().phase = OperationPhase::NeedsRemoveStateReset {
            summary: RemoveIssueSummary {
                backup_missing: 4,
                ..RemoveIssueSummary::default()
            },
        };
        app.result_selected = 0;

        app.activate_remove_state_reset_result();

        assert!(!state.ownership_path.exists());
        assert!(!state.backup_root.exists());
        assert!(!state.staging_root.exists());
        assert_eq!(fs::read(&game_file).unwrap(), b"patched-game-file");
        assert!(matches!(
            app.operation.as_ref().unwrap().phase,
            OperationPhase::Finished { success: true, .. }
        ));
    }

    #[test]
    fn settings_include_log_folder_action() {
        assert_eq!(SETTINGS_ITEM_COUNT, 6);
        let mut app = app(UriAction::Menu);
        app.screen = Screen::Settings;
        app.settings_selected = 5;
        app.activate_settings();
        assert_eq!(app.screen, Screen::Main);
    }

    #[test]
    fn keyboard_selection_wraps() {
        let mut app = app(UriAction::Menu);
        app.main_selected = 0;
        app.handle_main_key(KeyCode::Up);
        assert_eq!(app.main_selected, MAIN_ITEMS.len() - 1);
        app.handle_main_key(KeyCode::Down);
        assert_eq!(app.main_selected, 0);
    }

    #[test]
    fn numeric_keys_do_not_activate_main_menu_items() {
        let mut app = app(UriAction::Menu);
        app.main_selected = 2;
        app.handle_main_key(KeyCode::Char('1'));
        assert_eq!(app.screen, Screen::Main);
        assert_eq!(app.main_selected, 2);
        assert!(app.operation.is_none());
        assert!(!app.should_quit);
    }

    #[test]
    fn install_progress_updates_version_and_bars() {
        let mut app = app(UriAction::Menu);
        app.update_install_progress(InstallProgress::Selected {
            patch_version: "v3.2.0_r116_p0".into(),
            files: vec![PatchFileInfo {
                download_name: "data.unity3d.gz".into(),
                install_path: "data.unity3d".into(),
                download_size: 10,
                install_size: 20,
            }],
            download_total: 10,
            install_total: 20,
        });
        app.update_install_progress(InstallProgress::Downloading {
            file_index: 1,
            file_count: 1,
            file_name: "data.unity3d.gz".into(),
            current: 5,
            total: 10,
        });
        app.update_install_progress(InstallProgress::Applying {
            file_index: 1,
            file_count: 1,
            path: "data.unity3d".into(),
            phase: ApplyPhase::Copying,
            current: 8,
            total: 20,
        });

        assert_eq!(
            app.install_progress.patch_version.as_deref(),
            Some("v3.2.0_r116_p0")
        );
        assert_eq!(app.install_progress.download_current, 5);
        assert_eq!(app.install_progress.download_total, 10);
        assert_eq!(app.install_progress.apply_current, 8);
        assert_eq!(app.install_progress.apply_total, 20);
        assert!(app.install_progress.status.contains("data.unity3d"));
    }

    #[test]
    fn mouse_hit_testing_uses_rendered_rectangles() {
        assert!(contains(Rect::new(10, 5, 20, 2), 10, 5));
        assert!(contains(Rect::new(10, 5, 20, 2), 29, 6));
        assert!(!contains(Rect::new(10, 5, 20, 2), 30, 6));
    }
}
