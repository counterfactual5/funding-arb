#![cfg_attr(
    all(not(debug_assertions), target_os = "windows"),
    windows_subsystem = "windows"
)]

use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::Manager;

/// Python backend process handle (wrapped in Mutex for thread-safe access)
struct Backend(Mutex<Option<Child>>);

/// Start the Python FastAPI backend as a child process
fn start_backend() -> Result<Child, String> {
    let python = if cfg!(target_os = "windows") {
        "python"
    } else {
        "python3"
    };

    Command::new(python)
        .args([
            "-m", "uvicorn",
            "server.main:app",
            "--host", "127.0.0.1",
            "--port", "8787",
        ])
        .spawn()
        .map_err(|e| format!("启动后端失败: {}", e))
}

#[tauri::command]
fn get_backend_status() -> String {
    "running".into()
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            match start_backend() {
                Ok(child) => {
                    println!("[backend] Python server started (pid: {})", child.id());
                    app.manage(Backend(Mutex::new(Some(child))));
                }
                Err(e) => {
                    eprintln!("[backend] {}", e);
                    eprintln!("[backend] 前端将使用模拟数据");
                    app.manage(Backend(Mutex::new(None)));
                }
            }
            Ok(())
        })
        .on_window_event(|event| {
            if let tauri::WindowEvent::Destroyed = event.event() {
                if let Some(backend) = event.window().try_state::<Backend>() {
                    if let Ok(mut guard) = backend.0.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                            println!("[backend] Python server stopped");
                        }
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![get_backend_status])
        .run(tauri::generate_context!())
        .expect("启动 Tauri 应用失败");
}
