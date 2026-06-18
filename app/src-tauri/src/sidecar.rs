//! Long-lived Python sidecar manager: spawn, NDJSON request/response, cancellation.
//! Reference behavior per spec sections 3.2, 3.5, 3.6.

use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use anyhow::{anyhow, Context, Result};
use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Runtime};

const EVENT_NAME: &str = "contextful-event";

#[derive(Clone, Serialize)]
pub struct SidecarEvent {
    pub id: Option<String>,
    pub event: String,
    pub data: Value,
}

pub struct SidecarManager {
    sidecar_dir: PathBuf,
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
    events_rx: Mutex<Option<Receiver<Value>>>,
    cancel_flag: Arc<AtomicBool>,
}

impl SidecarManager {
    pub fn new(sidecar_dir: PathBuf) -> Self {
        Self {
            sidecar_dir,
            child: Mutex::new(None),
            stdin: Mutex::new(None),
            events_rx: Mutex::new(None),
            cancel_flag: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn start(&self) -> Result<()> {
        let mut child_guard = self.child.lock().unwrap();
        if child_guard.is_some() {
            return Ok(());
        }
        self.cancel_flag.store(false, Ordering::SeqCst);

        let mut command = if cfg!(debug_assertions) {
            let venv_python = if cfg!(windows) {
                self.sidecar_dir.join(".venv").join("Scripts").join("python.exe")
            } else {
                self.sidecar_dir.join(".venv").join("bin").join("python")
            };
            let python = if venv_python.exists() {
                venv_python
            } else {
                PathBuf::from("python")
            };
            let mut cmd = Command::new(python);
            cmd.args(["-m", "contextful_sidecar"])
                .current_dir(&self.sidecar_dir)
                .env("PYTHONPATH", self.sidecar_dir.join("src"))
                .env("PYTHONUTF8", "1")
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::inherit());
            cmd
        } else {
            let mut cmd = Command::new(release_sidecar_path()?);
            cmd.stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::null());
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                const CREATE_NO_WINDOW: u32 = 0x0800_0000;
                cmd.creation_flags(CREATE_NO_WINDOW);
            }
            cmd
        };

        let mut child = command.spawn().context("spawn sidecar")?;
        let stdout = child.stdout.take().context("sidecar stdout")?;
        let stdin = child.stdin.take().context("sidecar stdin")?;
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                if let Ok(value) = serde_json::from_str::<Value>(&line) {
                    let _ = tx.send(value); // drop malformed lines silently
                }
            }
        });
        *self.stdin.lock().unwrap() = Some(stdin);
        *self.events_rx.lock().unwrap() = Some(rx);
        *child_guard = Some(child);
        Ok(())
    }

    /// Cooperative cancel: signal the active request loop and tell the sidecar to cancel.
    pub fn cancel(&self) {
        self.cancel_flag.store(true, Ordering::SeqCst);
        if let Ok(mut g) = self.stdin.lock() {
            if let Some(ref mut stdin) = *g {
                let _ = writeln!(stdin, r#"{{"method":"cancel"}}"#);
                let _ = stdin.flush();
            }
        }
    }

    /// Hard stop (app shutdown): cancel, then kill the child.
    pub fn stop(&self) {
        self.cancel();
        thread::sleep(Duration::from_millis(300));
        if let Ok(mut g) = self.child.lock() {
            if let Some(mut child) = g.take() {
                let _ = child.kill();
            }
        }
        *self.stdin.lock().unwrap() = None;
        *self.events_rx.lock().unwrap() = None;
    }

    /// Send a request and block until a matching result/error, emitting streamed events.
    pub fn request<R: Runtime>(
        &self,
        app: &AppHandle<R>,
        method: &str,
        params: Value,
    ) -> Result<Value> {
        self.start()?;
        self.cancel_flag.store(false, Ordering::SeqCst);

        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        let req_id = format!("req-{nanos}");
        let payload = json!({"id": req_id, "method": method, "params": params});

        {
            let mut g = self.stdin.lock().unwrap();
            let stdin = g.as_mut().context("sidecar not running")?;
            writeln!(stdin, "{payload}").context("write request")?;
            stdin.flush().context("flush request")?;
        }

        let rx_guard = self.events_rx.lock().unwrap();
        let rx = rx_guard.as_ref().context("sidecar not running")?;

        loop {
            if self.cancel_flag.load(Ordering::SeqCst) {
                return Err(anyhow!("cancelled"));
            }
            match rx.recv_timeout(Duration::from_millis(100)) {
                Ok(value) => {
                    let id_matches = value
                        .get("id")
                        .and_then(Value::as_str)
                        .map(|id| id == req_id)
                        .unwrap_or(false);
                    if !id_matches {
                        continue; // stale event from a previous/cancelled request
                    }
                    if let Some(event) = value.get("event").and_then(Value::as_str) {
                        let _ = app.emit(
                            EVENT_NAME,
                            SidecarEvent {
                                id: value.get("id").and_then(Value::as_str).map(String::from),
                                event: event.to_string(),
                                data: value.get("data").cloned().unwrap_or(Value::Null),
                            },
                        );
                        continue;
                    }
                    if let Some(err) = value.get("error").and_then(Value::as_str) {
                        if err == "cancelled" {
                            return Err(anyhow!("cancelled"));
                        }
                        return Err(anyhow!(err.to_string()));
                    }
                    if let Some(result) = value.get("result") {
                        return Ok(result.clone());
                    }
                }
                Err(RecvTimeoutError::Timeout) => continue,
                Err(RecvTimeoutError::Disconnected) => {
                    return Err(anyhow!("sidecar disconnected"));
                }
            }
        }
    }
}

fn release_sidecar_path() -> Result<PathBuf> {
    let exe = std::env::current_exe()?;
    let dir = exe.parent().context("exe parent dir")?;
    let candidates: &[&str] = if cfg!(windows) {
        &[
            "contextful-sidecar-x86_64-pc-windows-msvc.exe",
            "contextful-sidecar.exe",
        ]
    } else if cfg!(target_os = "macos") {
        &[
            "contextful-sidecar-aarch64-apple-darwin",
            "contextful-sidecar-x86_64-apple-darwin",
            "contextful-sidecar",
        ]
    } else {
        &[
            "contextful-sidecar-x86_64-unknown-linux-gnu",
            "contextful-sidecar",
        ]
    };
    for name in candidates {
        let p = dir.join(name);
        if p.exists() {
            return Ok(p);
        }
    }
    Err(anyhow!("sidecar binary not found in {}", dir.display()))
}
