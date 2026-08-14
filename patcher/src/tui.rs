use std::io;
use std::path::{Path, PathBuf};
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

use crate::android::{
    AndroidInstallOutcome, AndroidProgress, AndroidService, PLAY_INSTALLER_PACKAGE,
};
use crate::game::GameRoute;
use crate::install::ApplyPhase;
use crate::service::{
    InstallOutcome, InstallProgress, PatchFileInfo, PatcherPaths,
    install_latest_compatible_with_progress, install_roots, installed_patch_info,
    remove_installed_patch,
};
use crate::settings::AppSettings;
use crate::uri::{UriAction, UriRequest};

const MIN_WIDTH: u16 = 72;
const MIN_HEIGHT: u16 = 27;
const MAIN_ITEMS: [&str; 5] = [
    "Android 패치 / 업데이트",
    "PC(Steam) 패치 설치 / 업데이트",
    "PC(Steam) 패치 제거",
    "PC(Steam) 설정",
    "종료",
];
const RESULT_ITEMS: [&str; 2] = ["메인 메뉴", "종료"];
const REINSTALL_ITEMS: [&str; 2] = ["기존 앱 제거 후 계속", "취소하고 메인 메뉴"];

#[derive(Debug, Clone, Copy)]
pub struct RemoteEndpoints {
    pub patch_release_index: &'static str,
    pub android_apk_index: &'static str,
    pub android_release_base: &'static str,
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
    AndroidInstall,
    SteamInstall,
    SteamRemove,
}

impl OperationKind {
    fn title(self) -> &'static str {
        match self {
            Self::AndroidInstall => "Android 패치 / 업데이트",
            Self::SteamInstall => "PC(Steam) 패치 설치 / 업데이트",
            Self::SteamRemove => "PC(Steam) 패치 제거",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum OperationPhase {
    Pending,
    Running,
    NeedsReinstall { message: String },
    Finished { success: bool, message: String },
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct OperationState {
    kind: OperationKind,
    route: GameRoute,
    protocol_request: bool,
    force_reinstall: bool,
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

#[derive(Debug, Clone, PartialEq, Eq)]
struct AndroidUiProgress {
    device: Option<String>,
    status: String,
    current: u64,
    total: u64,
}

impl Default for AndroidUiProgress {
    fn default() -> Self {
        Self {
            device: None,
            status: "기기를 확인할 준비가 되었습니다.".into(),
            current: 0,
            total: 0,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum HitTarget {
    Main(usize),
    Settings(usize),
    Result(usize),
    Reinstall(usize),
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
    android_progress: AndroidUiProgress,
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
            android_progress: AndroidUiProgress::default(),
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
        match kind {
            OperationKind::AndroidInstall => self.android_progress = AndroidUiProgress::default(),
            OperationKind::SteamInstall => self.install_progress = InstallUiProgress::default(),
            OperationKind::SteamRemove => {}
        }
        self.operation = Some(OperationState {
            kind,
            route: route_override.unwrap_or_else(|| self.settings.selected_route()),
            protocol_request,
            force_reinstall: false,
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
                    ApplyPhase::BackingUp => "원본 백업",
                    ApplyPhase::Copying => "패치 적용",
                    ApplyPhase::VerifyingInstalled => "적용 후 검증",
                };
                self.install_progress.status =
                    format!("{action} 중 ({file_index}/{file_count}): {path}");
            }
        }
    }

    fn update_android_progress(&mut self, event: AndroidProgress) {
        match event {
            AndroidProgress::PreparingAdb => {
                self.android_progress.status = "Android 연결 도구를 준비하는 중입니다...".into();
            }
            AndroidProgress::DiscoveringDevices => {
                self.android_progress.status =
                    "USB 기기와 앱플레이어를 자동으로 찾는 중입니다...".into();
            }
            AndroidProgress::DeviceSelected { name } => {
                self.android_progress.device = Some(name);
                self.android_progress.status = "패치할 기기를 확인했습니다.".into();
            }
            AndroidProgress::ResolvingApk => {
                self.android_progress.status =
                    "최신 Android 패치 APK를 확인하는 중입니다...".into();
            }
            AndroidProgress::DownloadingApk { current, total } => {
                self.android_progress.current = current;
                self.android_progress.total = total;
                self.android_progress.status = "Android 패치 APK를 다운로드하는 중입니다...".into();
            }
            AndroidProgress::Installing => {
                self.android_progress.status =
                    "Google Play installer 정보로 APK를 설치하는 중입니다...".into();
            }
            AndroidProgress::RemovingOfficialInstall => {
                self.android_progress.status = "기존 공식판을 제거하는 중입니다...".into();
            }
            AndroidProgress::Verifying => {
                self.android_progress.status =
                    "설치 결과와 installer를 검증하는 중입니다...".into();
            }
        }
    }

    fn perform_remove(&self, route: GameRoute) -> Result<String, String> {
        let game = self
            .settings
            .installation_for(route)
            .map_err(|error| error.to_string())?;
        let roots = install_roots(&game);
        match remove_installed_patch(&self.paths, &roots, game.route)
            .map_err(|error| error.to_string())?
        {
            None => Ok("설치된 패치 기록이 없습니다.".into()),
            Some(report) => Ok(format!(
                "패치를 제거했습니다. 삭제 {}개, 복구 {}개",
                report.removed, report.restored
            )),
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
            "게임 route를 {}로 변경했습니다.",
            self.settings.selected_route().as_str()
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
            self.notice = Some(format!("자동 감지에 실패했습니다: {error}"));
            return;
        }
        if let Err(error) = self.settings.save(&self.paths.settings_path) {
            self.settings = old;
            self.notice = Some(format!("설정을 저장하지 못했습니다: {error}"));
            return;
        }
        self.notice = Some("Steam과 LocalLow 경로를 다시 감지했습니다.".into());
    }

    fn activate_main(&mut self) {
        match self.main_selected {
            0 => self.start_operation(OperationKind::AndroidInstall, false, None),
            1 => self.start_operation(OperationKind::SteamInstall, false, None),
            2 => self.start_operation(OperationKind::SteamRemove, false, None),
            3 => {
                self.screen = Screen::Settings;
                self.notice = None;
            }
            4 => self.should_quit = true,
            _ => {}
        }
    }

    fn activate_settings(&mut self) {
        match self.settings_selected {
            0 => self.select_next_route(),
            1 => self.begin_path_input(PathKind::Steam),
            2 => self.begin_path_input(PathKind::LocalLow),
            3 => self.redetect_paths(),
            4 => {
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

    fn activate_reinstall_result(&mut self) {
        match self.result_selected {
            0 => {
                if let Some(operation) = &mut self.operation {
                    operation.force_reinstall = true;
                    operation.phase = OperationPhase::Pending;
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
            KeyCode::Up => self.settings_selected = previous(self.settings_selected, 5),
            KeyCode::Down => self.settings_selected = next(self.settings_selected, 5),
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

    fn handle_operation_key(&mut self, code: KeyCode) {
        let phase = self
            .operation
            .as_ref()
            .map(|operation| operation.phase.clone());
        match phase {
            Some(OperationPhase::NeedsReinstall { .. }) => match code {
                KeyCode::Up => {
                    self.result_selected = previous(self.result_selected, REINSTALL_ITEMS.len())
                }
                KeyCode::Down => {
                    self.result_selected = next(self.result_selected, REINSTALL_ITEMS.len())
                }
                KeyCode::Enter => self.activate_reinstall_result(),
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
                    Some(HitTarget::Reinstall(index)) => {
                        self.result_selected = index;
                        self.activate_reinstall_result();
                    }
                    None => {}
                }
            }
            MouseEventKind::ScrollUp => match self.screen {
                Screen::Main => self.main_selected = previous(self.main_selected, MAIN_ITEMS.len()),
                Screen::Settings => self.settings_selected = previous(self.settings_selected, 5),
                Screen::Operation => {
                    self.result_selected = previous(self.result_selected, RESULT_ITEMS.len());
                }
                Screen::PathInput(_) => {}
            },
            MouseEventKind::ScrollDown => match self.screen {
                Screen::Main => self.main_selected = next(self.main_selected, MAIN_ITEMS.len()),
                Screen::Settings => self.settings_selected = next(self.settings_selected, 5),
                Screen::Operation => {
                    self.result_selected = next(self.result_selected, RESULT_ITEMS.len());
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
    let Some((kind, route, force_reinstall)) = app
        .operation
        .as_ref()
        .map(|state| (state.kind, state.route, state.force_reinstall))
    else {
        return;
    };

    if kind == OperationKind::AndroidInstall {
        let result = perform_android_install(terminal, app, force_reinstall);
        let phase = match result {
            Ok(AndroidInstallOutcome::Installed {
                device,
                game_version,
            }) => OperationPhase::Finished {
                success: true,
                message: format!(
                    "{}에 Astral Party Android 한국어판 {} 설치를 완료했습니다. installer={} 검증도 통과했습니다.",
                    device.display_name(),
                    game_version,
                    PLAY_INSTALLER_PACKAGE
                ),
            },
            Ok(AndroidInstallOutcome::NeedsReinstall {
                device,
                game_version,
            }) => {
                app.result_selected = 0;
                OperationPhase::NeedsReinstall {
                    message: format!(
                        "{}에 Google Play 공식판 또는 다른 서명의 APK가 설치되어 있습니다. 한국어판 {}은 다른 서명을 사용하므로 최초 1회 기존 앱을 제거해야 합니다. 제거하면 앱 로컬 데이터가 삭제될 수 있습니다. 게임 계정 연동을 확인한 뒤 계속하세요.",
                        device.display_name(),
                        game_version
                    ),
                }
            }
            Err(message) => OperationPhase::Finished {
                success: false,
                message,
            },
        };
        if let Some(operation) = &mut app.operation {
            operation.phase = phase;
        }
        return;
    }

    let result = match kind {
        OperationKind::SteamInstall => perform_install(terminal, app, route),
        OperationKind::SteamRemove => app.perform_remove(route),
        OperationKind::AndroidInstall => unreachable!(),
    };
    if let Some(operation) = &mut app.operation {
        operation.phase = match result {
            Ok(message) => OperationPhase::Finished {
                success: true,
                message,
            },
            Err(message) => OperationPhase::Finished {
                success: false,
                message,
            },
        };
    }
}

fn perform_android_install(
    terminal: &mut DefaultTerminal,
    app: &mut App,
    force_reinstall: bool,
) -> Result<AndroidInstallOutcome, String> {
    let service = AndroidService::new(
        app.paths.state_root.clone(),
        app.endpoints.android_apk_index,
        app.endpoints.android_release_base,
    );
    let mut last_draw = Instant::now();
    service
        .install_latest_with_progress(force_reinstall, |event| {
            let immediate = !matches!(event, AndroidProgress::DownloadingApk { .. });
            app.update_android_progress(event);
            if immediate || last_draw.elapsed() >= Duration::from_millis(50) {
                let _ = terminal.draw(|frame| render(frame, app));
                last_draw = Instant::now();
            }
        })
        .map_err(|error| {
            let message = error.to_string();
            app.android_progress.status = format!("실패: {message}");
            message
        })
}

fn perform_install(
    terminal: &mut DefaultTerminal,
    app: &mut App,
    route: GameRoute,
) -> Result<String, String> {
    let paths = app.paths.clone();
    let settings = app.settings.clone();
    let release_index_url = app.endpoints.patch_release_index;
    let game = settings
        .installation_for(route)
        .map_err(|error| error.to_string())?;
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
        .map_err(|error| error.to_string())?;
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
            "AstralAutoPatcher v{}\n\n터미널 창이 너무 작습니다.\n최소 {} x {} 이상으로 늘려주세요.\n\n현재 크기: {} x {}",
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
            Constraint::Length(10),
            Constraint::Min(10),
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
    let (game_version, catalog) = match app.settings.installation() {
        Ok(game) => (game.catalog.version, game.catalog.hash),
        Err(error) => (format!("감지 실패 ({error})"), "-".into()),
    };
    let route = app.settings.selected_route();
    let state = app.paths.route_state(route);
    let installed = match installed_patch_info(&state.ownership_path) {
        Ok(Some(info)) => info.patch_version,
        Ok(None) => "미설치".into(),
        Err(error) => format!("상태 확인 실패 ({error})"),
    };
    let android_device = app
        .android_progress
        .device
        .clone()
        .unwrap_or_else(|| "USB / MuMu / BlueStacks / LDPlayer 자동 감지".into());

    let title = format!(" AstralAutoPatcher v{} ", env!("CARGO_PKG_VERSION"));
    let block = Block::default().borders(Borders::ALL).title(title);
    let inner = block.inner(area);
    frame.render_widget(block, area);

    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Min(6),
        ])
        .split(inner);
    frame.render_widget(
        Paragraph::new(status_text_line(
            "Patcher 경로",
            app.installed_exe.display().to_string(),
        )),
        rows[0],
    );

    let columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(48),
            Constraint::Length(2),
            Constraint::Percentage(52),
        ])
        .split(rows[2]);

    let android = Text::from(vec![
        Line::from(Span::styled(
            "Android",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
        status_text_line("기기", android_device),
        status_text_line("상태", app.android_progress.status.clone()),
        status_text_line("설치 소스", PLAY_INSTALLER_PACKAGE.into()),
    ]);
    frame.render_widget(Paragraph::new(android), columns[0]);

    let steam = Text::from(vec![
        Line::from(Span::styled(
            "PC (Steam)",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )),
        status_text_line("Route", route.as_str().to_string()),
        status_text_line("실행 경로", display_steam_route_path(&app.settings)),
        status_text_line("LocalLow", display_path(app.settings.locallow_root())),
        status_text_line("게임", format!("{game_version} · catalog {catalog}")),
        status_text_line("패치", installed),
    ]);
    frame.render_widget(Paragraph::new(steam), columns[2]);
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
            Cell::from("게임 route"),
            Cell::from(format!(
                "[{}]",
                app.settings.selected_route().display_name()
            )),
        ]),
        Row::new(vec![
            Cell::from("Steam 실행 경로"),
            Cell::from(format!("[{}]", display_steam_route_path(&app.settings))),
        ]),
        Row::new(vec![
            Cell::from("LocalLow 게임 경로"),
            Cell::from(format!("[{}]", display_path(app.settings.locallow_root()))),
        ]),
        Row::new(vec![Cell::from("게임 경로 자동 감지"), Cell::from("")]),
        Row::new(vec![Cell::from("돌아가기"), Cell::from("")]),
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
    add_list_hits(&mut app.hit_regions, inner, 5, HitTarget::Settings);
}

fn render_path_input(frame: &mut Frame<'_>, app: &App, area: Rect, kind: PathKind) {
    let route = app.settings.selected_route();
    let (title, help, current) = match kind {
        PathKind::Steam => (
            " Steam 게임 경로 입력 ",
            format!(
                "Steam의 'Astral Party', '{}', 또는 '{}' 경로를 입력하세요.",
                route.executable_dir(),
                route.data_dir()
            ),
            display_steam_route_path(&app.settings),
        ),
        PathKind::LocalLow => (
            " LocalLow 게임 경로 입력 ",
            format!(
                "LocalLow의 '{}' 폴더 경로를 입력하세요.",
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
                OperationKind::AndroidInstall => {
                    render_android_running(frame, app, area, pending);
                    return;
                }
                OperationKind::SteamInstall => {
                    render_install_running(frame, app, area, &operation, pending);
                    return;
                }
                OperationKind::SteamRemove => {}
            }
            let status = if pending {
                "현재 상태 정보를 표시했습니다. 작업을 시작합니다..."
            } else {
                "설치 기록을 검증하고 PC 패치를 제거하는 중입니다..."
            };
            let text = Text::from(vec![
                Line::from(vec![
                    Span::styled(
                        "대상 route: ",
                        Style::default().add_modifier(Modifier::BOLD),
                    ),
                    Span::raw(operation.route.as_str()),
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
        OperationPhase::NeedsReinstall { message } => {
            let chunks = Layout::default()
                .direction(Direction::Vertical)
                .constraints([Constraint::Min(8), Constraint::Length(4)])
                .split(area);
            let text = Text::from(vec![
                Line::from(Span::styled(
                    "최초 설치 확인 필요",
                    Style::default()
                        .fg(Color::Yellow)
                        .add_modifier(Modifier::BOLD),
                )),
                Line::from(""),
                Line::from(message),
                Line::from(""),
                Line::from("계속하면 기존 Astral Party 앱을 제거한 뒤 한국어판을 설치합니다."),
            ]);
            frame.render_widget(
                Paragraph::new(text)
                    .block(Block::default().borders(Borders::ALL).title(title))
                    .wrap(Wrap { trim: false }),
                chunks[0],
            );
            let result_block = Block::default().borders(Borders::ALL).title(" 선택 ");
            let inner = result_block.inner(chunks[1]);
            let list = List::new(REINSTALL_ITEMS.map(ListItem::new))
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
                REINSTALL_ITEMS.len(),
                HitTarget::Reinstall,
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

fn render_android_running(frame: &mut Frame<'_>, app: &App, area: Rect, pending: bool) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(8), Constraint::Length(3)])
        .split(area);
    let device = app
        .android_progress
        .device
        .as_deref()
        .unwrap_or("자동 감지 중");
    let status = if pending {
        "Android 자동 패치를 시작합니다..."
    } else {
        app.android_progress.status.as_str()
    };
    let text = Text::from(vec![
        Line::from(vec![
            Span::styled("기기: ", Style::default().add_modifier(Modifier::BOLD)),
            Span::raw(device),
        ]),
        Line::from(vec![
            Span::styled("설치 방식: ", Style::default().add_modifier(Modifier::BOLD)),
            Span::raw(format!(
                "ADB 자동 관리 · installer={PLAY_INSTALLER_PACKAGE}"
            )),
        ]),
        Line::from(""),
        Line::from(Span::styled(status, Style::default().fg(Color::Yellow))),
        Line::from(""),
        Line::from("실제 기기: USB 디버깅과 최초 RSA 승인만 필요합니다."),
        Line::from("앱플레이어: 실행 중인 MuMu/BlueStacks/LDPlayer를 자동 탐색합니다."),
    ]);
    frame.render_widget(
        Paragraph::new(text)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(" Android 원클릭 패치 "),
            )
            .wrap(Wrap { trim: false }),
        chunks[0],
    );

    let ratio = if app.android_progress.total == 0 {
        0.0
    } else {
        (app.android_progress.current as f64 / app.android_progress.total as f64).clamp(0.0, 1.0)
    };
    let label = if app.android_progress.total == 0 {
        "APK 준비".to_string()
    } else {
        format!(
            "APK {} / {}",
            format_bytes(app.android_progress.current),
            format_bytes(app.android_progress.total)
        )
    };
    frame.render_widget(
        Gauge::default()
            .block(Block::default().borders(Borders::ALL).title(" 다운로드 "))
            .ratio(ratio)
            .label(label),
        chunks[1],
    );
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
                "대상 route: ",
                Style::default().add_modifier(Modifier::BOLD),
            ),
            Span::raw(operation.route.as_str()),
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
        Screen::Main => "↑↓ 이동 · Enter 선택 · 마우스 클릭 · Esc/Q 종료",
        Screen::Settings => "↑↓ 이동 · Enter 선택 · 마우스 클릭 · Esc 뒤로가기",
        Screen::PathInput(_) => "경로 입력 · Enter 저장 · Esc 취소",
        Screen::Operation => "작업 완료 후 ↑↓/Enter 또는 마우스로 선택 · Esc 메인 메뉴",
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
                android_apk_index: "https://example.test/android-apk-index.json",
                android_release_base: "https://example.test/releases/download",
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
    fn android_is_the_primary_main_menu_action() {
        let mut app = app(UriAction::Menu);
        app.main_selected = 0;
        app.activate_main();
        let operation = app.operation.as_ref().unwrap();
        assert_eq!(operation.kind, OperationKind::AndroidInstall);
        assert!(!operation.force_reinstall);
        assert_eq!(app.screen, Screen::Operation);
    }

    #[test]
    fn reinstall_confirmation_requeues_android_operation() {
        let mut app = app(UriAction::Menu);
        app.start_operation(OperationKind::AndroidInstall, false, None);
        app.operation.as_mut().unwrap().phase = OperationPhase::NeedsReinstall {
            message: "confirm".into(),
        };
        app.result_selected = 0;
        app.activate_reinstall_result();
        let operation = app.operation.as_ref().unwrap();
        assert!(operation.force_reinstall);
        assert!(matches!(operation.phase, OperationPhase::Pending));
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
            patch_version: "v3.2.0-r116-pre".into(),
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
            Some("v3.2.0-r116-pre")
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
