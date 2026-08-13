fn main() {
    if let Err(error) = astral_auto_patcher::cli::run() {
        eprintln!("AstralAutoPatcher 오류: {error}");
        std::process::exit(1);
    }
}
