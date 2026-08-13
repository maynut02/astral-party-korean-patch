use std::io;
use std::path::{Path, PathBuf};

use crossterm::event::{
    self, DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture,
    Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use crossterm::execute;
use ratatui::layout::{Alignment, Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, List, ListItem, ListState, Paragraph, Wrap};
use ratatui::{DefaultTerminal, Frame};

use crate::service::{
    InstallOutcome, PatcherPaths, install_latest_compatible, install_roots, installed_patch_info,
    remove_installed_patch,
};
use crate::settings::AppSettings;
use crate::uri::UriAction;

const MIN_WIDTH: u16 = 72;
const MIN_HEIGHT: u16 = 24;
const MAIN_ITEMS: [&str; 4] = ["패치 설치 / 업데이트", "패치 제거", "프로그램 설정", "종료"];
const RESULT_ITEMS: [&str; 2] = ["메인 메뉴", "종료"];

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
    Install,
    Remove,
}

impl OperationKind {
    fn title(self) -> &'static str {
        match self {
            Self::Install => "패치 설치 / 업데이트",
            Self::Remove => "패치 제거",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum OperationPhase {
    Pending,
    Running,
    Finished { success: bool, message: String },
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct OperationState {
    kind: OperationKind,
    protocol_request: bool,
    phase: OperationPhase,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum HitTarget {
    Main(usize),
    Settings(usize),
    Result(usize),
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
    release_index_url: &'static str,
    screen: Screen,
    main_selected: usize,
    settings_selected: usize,
    result_selected: usize,
    input: String,
    operation: Option<OperationState>,
    notice: Option<String>,
    hit_regions: Vec<HitRegion>,
    should_quit: bool,
}

impl App {
    fn new(
        paths: PatcherPaths,
        settings: AppSettings,
        installed_exe: PathBuf,
        initial_action: UriAction,
        startup_notice: Option<String>,
        release_index_url: &'static str,
    ) -> Self {
        let mut app = Self {
            paths,
            settings,
            installed_exe,
            release_index_url,
            screen: Screen::Main,
            main_selected: 0,
            settings_selected: 0,
            result_selected: 0,
            input: String::new(),
            operation: None,
            notice: startup_notice,
            hit_regions: Vec::new(),
            should_quit: false,
        };
        match initial_action {
            UriAction::Menu => {}
            UriAction::Install => app.start_operation(OperationKind::Install, true),
            UriAction::Remove => app.start_operation(OperationKind::Remove, true),
            UriAction::Settings => {
                app.screen = Screen::Settings;
                app.notice = Some("웹사이트에서 프로그램 설정을 요청했습니다.".into());
            }
        }
        app
    }

    fn start_operation(&mut self, kind: OperationKind, protocol_request: bool) {
        self.operation = Some(OperationState {
            kind,
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

    fn execute_operation(&mut self) {
        let Some(kind) = self.operation.as_ref().map(|state| state.kind) else {
            return;
        };
        let result = match kind {
            OperationKind::Install => self.perform_install(),
            OperationKind::Remove => self.perform_remove(),
        };
        if let Some(operation) = &mut self.operation {
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

    fn perform_install(&self) -> Result<String, String> {
        let game = self
            .settings
            .installation()
            .map_err(|error| error.to_string())?;
        match install_latest_compatible(
            self.release_index_url,
            &self.settings.channel,
            &self.paths,
            &game,
        )
        .map_err(|error| error.to_string())?
        {
            InstallOutcome::AlreadyInstalled(info) => Ok(format!(
                "{} 패치가 이미 설치되어 있습니다.",
                info.patch_version
            )),
            InstallOutcome::Installed(summary) => Ok(format!(
                "패치 설치가 완료되었습니다. 새 파일 {}개, 교체 파일 {}개",
                summary.created, summary.modified
            )),
        }
    }

    fn perform_remove(&self) -> Result<String, String> {
        let game = self
            .settings
            .installation()
            .map_err(|error| error.to_string())?;
        let roots = install_roots(&game);
        match remove_installed_patch(&self.paths, &roots).map_err(|error| error.to_string())? {
            None => Ok("설치된 패치 기록이 없습니다.".into()),
            Some(report) => Ok(format!(
                "패치를 제거했습니다. 삭제 {}개, 복구 {}개",
                report.removed, report.restored
            )),
        }
    }

    fn path_changes_allowed(&mut self) -> bool {
        match installed_patch_info(&self.paths.ownership_path) {
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

    fn toggle_channel(&mut self) {
        let old = self.settings.clone();
        let next = if self.settings.channel == "stable" {
            "preview"
        } else {
            "stable"
        };
        if let Err(error) = self.settings.set_channel(next) {
            self.notice = Some(format!("패치 채널을 변경하지 못했습니다: {error}"));
            return;
        }
        if let Err(error) = self.settings.save(&self.paths.settings_path) {
            self.settings = old;
            self.notice = Some(format!("설정을 저장하지 못했습니다: {error}"));
            return;
        }
        self.notice = Some(format!(
            "패치 채널을 {}로 변경했습니다.",
            self.settings.channel
        ));
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
            0 => self.start_operation(OperationKind::Install, false),
            1 => self.start_operation(OperationKind::Remove, false),
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
            0 => self.begin_path_input(PathKind::Steam),
            1 => self.begin_path_input(PathKind::LocalLow),
            2 => self.toggle_channel(),
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
            KeyCode::Char('1') => {
                self.main_selected = 0;
                self.activate_main();
            }
            KeyCode::Char('2') => {
                self.main_selected = 1;
                self.activate_main();
            }
            KeyCode::Char('3') => {
                self.main_selected = 2;
                self.activate_main();
            }
            KeyCode::Char('0') => self.should_quit = true,
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
        let finished = self
            .operation
            .as_ref()
            .is_some_and(|operation| matches!(&operation.phase, OperationPhase::Finished { .. }));
        if !finished {
            return;
        }
        match code {
            KeyCode::Up => {
                self.result_selected = previous(self.result_selected, RESULT_ITEMS.len())
            }
            KeyCode::Down => self.result_selected = next(self.result_selected, RESULT_ITEMS.len()),
            KeyCode::Enter => self.activate_result(),
            KeyCode::Esc => {
                self.operation = None;
                self.screen = Screen::Main;
            }
            KeyCode::Char('q') => self.should_quit = true,
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
    initial_action: UriAction,
    startup_notice: Option<String>,
    release_index_url: &'static str,
) -> io::Result<()> {
    ratatui::run(|terminal| {
        let _extras = TerminalExtras::enable()?;
        let mut app = App::new(
            paths,
            settings,
            installed_exe,
            initial_action,
            startup_notice,
            release_index_url,
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
            app.execute_operation();
            continue;
        }
        app.handle_event(event::read()?);
    }
    Ok(())
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
            Constraint::Min(9),
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
    let installed = match installed_patch_info(&app.paths.ownership_path) {
        Ok(Some(info)) => info.patch_version,
        Ok(None) => "미설치".into(),
        Err(error) => format!("상태 확인 실패 ({error})"),
    };
    let lines = vec![
        status_line("Patcher 경로", &app.installed_exe.display().to_string()),
        status_line(
            "Steam 경로",
            &display_path(app.settings.steam_game_root.as_deref()),
        ),
        status_line(
            "LocalLow 경로",
            &display_path(app.settings.locallow_root.as_deref()),
        ),
        status_line("패치 채널", &app.settings.channel),
        status_line("게임 버전", &game_version),
        status_line("Catalog", &catalog),
        status_line("설치 패치", &installed),
    ];
    let title = format!(" AstralAutoPatcher v{} ", env!("CARGO_PKG_VERSION"));
    frame.render_widget(
        Paragraph::new(Text::from(lines))
            .block(Block::default().borders(Borders::ALL).title(title)),
        area,
    );
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
    let labels = [
        format!(
            "Steam 게임 경로   [{}]",
            display_path(app.settings.steam_game_root.as_deref())
        ),
        format!(
            "LocalLow 게임 경로 [{}]",
            display_path(app.settings.locallow_root.as_deref())
        ),
        format!("패치 채널          [{}]", app.settings.channel),
        "게임 경로 자동 감지".into(),
        "돌아가기".into(),
    ];
    let block = Block::default()
        .borders(Borders::ALL)
        .title(" 프로그램 설정 ");
    let inner = block.inner(area);
    let list = List::new(labels.into_iter().map(ListItem::new))
        .block(block)
        .highlight_symbol("▶ ")
        .highlight_style(Style::default().add_modifier(Modifier::BOLD | Modifier::REVERSED));
    let mut state = ListState::default();
    state.select(Some(app.settings_selected));
    frame.render_stateful_widget(list, area, &mut state);
    add_list_hits(&mut app.hit_regions, inner, 5, HitTarget::Settings);
}

fn render_path_input(frame: &mut Frame<'_>, app: &App, area: Rect, kind: PathKind) {
    let (title, help, current) = match kind {
        PathKind::Steam => (
            " Steam 게임 경로 입력 ",
            "Steam의 'Astral Party' 폴더 경로를 입력하세요.",
            display_path(app.settings.steam_game_root.as_deref()),
        ),
        PathKind::LocalLow => (
            " LocalLow 게임 경로 입력 ",
            "LocalLow의 'AstralParty_INT' 폴더 경로를 입력하세요.",
            display_path(app.settings.locallow_root.as_deref()),
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
            let request = if operation.protocol_request {
                "웹사이트 프로토콜 요청"
            } else {
                "프로그램 메뉴 요청"
            };
            let status = if pending {
                "현재 상태 정보를 표시했습니다. 작업을 시작합니다..."
            } else {
                match operation.kind {
                    OperationKind::Install => "호환 패치를 확인하고 다운로드/검증하는 중입니다...",
                    OperationKind::Remove => "설치 기록을 검증하고 패치를 제거하는 중입니다...",
                }
            };
            let text = Text::from(vec![
                Line::from(vec![
                    Span::styled("요청: ", Style::default().add_modifier(Modifier::BOLD)),
                    Span::raw(request),
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

fn render_footer(frame: &mut Frame<'_>, app: &App, area: Rect) {
    let notice = app.notice.as_deref().unwrap_or("");
    let help = match app.screen {
        Screen::Main => "↑↓ 이동 · Enter 선택 · 마우스 클릭 · 1/2/3 단축키 · Esc/Q 종료",
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

fn status_line(label: &str, value: &str) -> Line<'static> {
    Line::from(vec![
        Span::styled(
            format!("{label:<13}"),
            Style::default().add_modifier(Modifier::BOLD),
        ),
        Span::raw(format!(": {value}")),
    ])
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

    fn app(action: UriAction) -> App {
        let temp = tempdir().unwrap();
        App::new(
            PatcherPaths::below(temp.path().join("state")),
            AppSettings::default(),
            temp.path().join("AstralAutoPatcher.exe"),
            action,
            None,
            "https://example.test/release-index.json",
        )
    }

    #[test]
    fn protocol_install_starts_on_operation_screen_before_execution() {
        let app = app(UriAction::Install);
        assert_eq!(app.screen, Screen::Operation);
        assert!(app.operation_pending());
        assert!(app.operation.as_ref().unwrap().protocol_request);
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
    fn mouse_hit_testing_uses_rendered_rectangles() {
        assert!(contains(Rect::new(10, 5, 20, 2), 10, 5));
        assert!(contains(Rect::new(10, 5, 20, 2), 29, 6));
        assert!(!contains(Rect::new(10, 5, 20, 2), 30, 6));
    }
}
