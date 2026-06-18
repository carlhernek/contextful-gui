//! App settings persisted via tauri-plugin-store (spec section 10.2).

use std::path::PathBuf;

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::{AppHandle, Runtime};
use tauri_plugin_store::StoreExt;

const STORE_FILE: &str = "settings.json";
const SETTINGS_KEY: &str = "settings";

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AppSettings {
    #[serde(default)]
    pub install_path: Option<String>,
    #[serde(default)]
    pub active_project: Option<String>,
}

pub fn load_settings<R: Runtime>(app: &AppHandle<R>) -> Result<AppSettings> {
    let store = app.store(STORE_FILE).context("open settings store")?;
    match store.get(SETTINGS_KEY) {
        Some(value) => serde_json::from_value(value).context("parse settings"),
        None => Ok(AppSettings::default()),
    }
}

pub fn save_settings<R: Runtime>(app: &AppHandle<R>, settings: &AppSettings) -> Result<()> {
    let store = app.store(STORE_FILE).context("open settings store")?;
    store.set(SETTINGS_KEY, serde_json::to_value(settings)?);
    store.save().context("persist settings store")?;
    Ok(())
}

pub fn set_install_path<R: Runtime>(app: &AppHandle<R>, path: &str) -> Result<AppSettings> {
    let mut s = load_settings(app)?;
    s.install_path = Some(path.to_string());
    save_settings(app, &s)?;
    Ok(s)
}

pub fn set_active_project<R: Runtime>(app: &AppHandle<R>, id: Option<&str>) -> Result<AppSettings> {
    let mut s = load_settings(app)?;
    s.active_project = id.map(|v| v.to_string());
    save_settings(app, &s)?;
    Ok(s)
}

/// Default install folder: {Documents}/contextful (fallback to home dir).
pub fn default_install_folder() -> PathBuf {
    let base = dirs_documents().or_else(dirs_home).unwrap_or_else(|| PathBuf::from("."));
    base.join("contextful")
}

pub fn settings_json<R: Runtime>(app: &AppHandle<R>) -> Value {
    let s = load_settings(app).unwrap_or_default();
    json!({
        "install_path": s.install_path,
        "active_project": s.active_project,
    })
}

fn dirs_documents() -> Option<PathBuf> {
    #[cfg(target_os = "windows")]
    {
        std::env::var_os("USERPROFILE").map(|p| PathBuf::from(p).join("Documents"))
    }
    #[cfg(not(target_os = "windows"))]
    {
        std::env::var_os("HOME").map(|p| PathBuf::from(p).join("Documents"))
    }
}

fn dirs_home() -> Option<PathBuf> {
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from)
}
