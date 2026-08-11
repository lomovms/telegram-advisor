use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;

use tauri::{Manager, RunEvent};

struct LocalApi(Mutex<Option<Child>>);

#[tauri::command]
fn open_gmail_thread(thread_id: String) -> Result<(), String> {
    if thread_id.is_empty() || !thread_id.chars().all(|character| character.is_ascii_hexdigit()) {
        return Err("Некорректный идентификатор цепочки Gmail".into());
    }
    let url = format!("https://mail.google.com/mail/u/0/#all/{thread_id}");
    Command::new("rundll32.exe")
        .args(["url.dll,FileProtocolHandler", &url])
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Не удалось открыть Gmail: {error}"))
}

#[tauri::command]
fn open_projects(profile_id: String) -> Result<(), String> {
    if !profile_id.is_empty() && profile_id.parse::<u64>().is_err() {
        return Err("Некорректный контакт для проектов".into());
    }
    let url = if profile_id.is_empty() {
        "http://127.0.0.1:18791/projects".to_owned()
    } else {
        format!("http://127.0.0.1:18791/projects?profile={profile_id}")
    };
    Command::new("rundll32.exe")
        .args(["url.dll,FileProtocolHandler", &url])
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("Не удалось открыть проекты: {error}"))
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![open_gmail_thread, open_projects])
        .setup(|app| {
            start_development_gateway();
            app.manage(LocalApi(Mutex::new(start_local_api(app))));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("не удалось запустить Telegram Advisor")
        .run(|app, event| {
            if let RunEvent::ExitRequested { .. } = event {
                if let Some(api) = app.try_state::<LocalApi>() {
                    if let Ok(mut child) = api.0.lock() {
                        if let Some(process) = child.as_mut() {
                            let _ = process.kill();
                        }
                    }
                }
            }
        });
}

fn start_development_gateway() {
    let project_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let script = project_root.join("codex-gateway/start-gateway.ps1");
    if !project_root.join(".venv/Scripts/pythonw.exe").exists() || !script.exists() {
        return;
    }

    let _ = Command::new("powershell.exe")
        .args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
        .arg(script)
        .current_dir(project_root)
        .status();
}

fn start_local_api(app: &tauri::App) -> Option<Child> {
    let project_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let development_python = project_root.join(".venv/Scripts/pythonw.exe");
    let force_packaged = std::env::var_os("PAIN_FORCE_PACKAGED").is_some();
    if !force_packaged && development_python.exists() && project_root.join("src/pain_assistant").exists() {
        return Command::new(development_python)
            .args(["-m", "pain_assistant.web_backend"])
            .current_dir(&project_root)
            .env("PYTHONPATH", project_root.join("src"))
            .spawn()
            .map(Some)
            .unwrap_or_else(|error| {
                eprintln!("Unable to start development API: {error}");
                None
            });
    }

    let resource_dir = app.path().resource_dir().ok()?;
    let data_dir = std::env::var_os("PAIN_ADVISOR_DATA_DIR")
        .map(PathBuf::from)
        .or_else(|| app.path().app_local_data_dir().ok())?;
    if std::fs::create_dir_all(&data_dir).is_err() {
        return None;
    }

    let backend = first_existing(&[
        resource_dir.join("resources/backend/telegram-advisor-backend.exe"),
        resource_dir.join("backend/telegram-advisor-backend.exe"),
    ])?;
    let codex = first_existing(&[
        resource_dir.join("resources/codex/vendor/x86_64-pc-windows-msvc/bin/codex.exe"),
        resource_dir.join("codex/vendor/x86_64-pc-windows-msvc/bin/codex.exe"),
    ])?;

    Command::new(backend)
        .current_dir(&data_dir)
        .env("ANALYSIS_BACKEND", "codex")
        .env("CODEX_BIN", codex)
        .env("APP_DB_PATH", data_dir.join("advisor.sqlite3"))
        .env("TELEGRAM_SESSION_PATH", data_dir.join("telegram.work.session"))
        .env("TELEGRAM_PERSONAL_SESSION_PATH", data_dir.join("telegram.personal.session"))
        .env("SCREENSHOT_DIR", data_dir.join("screenshots"))
        .env("LOCAL_OUTGOING_SCREENSHOT_DIR", data_dir.join("outgoing-screenshots"))
        .env("PAIN_ONBOARDING_REQUIRED", "1")
        .env("PAIN_IGNORE_DOTENV", "1")
        .spawn()
        .map(Some)
        .unwrap_or_else(|error| {
            eprintln!("Unable to start packaged API: {error}");
            None
        })
}

fn first_existing(candidates: &[PathBuf]) -> Option<PathBuf> {
    candidates.iter().find(|path| path.exists()).cloned()
}
