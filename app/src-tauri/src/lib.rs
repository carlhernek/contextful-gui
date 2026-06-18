//! Contextful Tauri core: command surface + app wiring (spec section 10).

mod prereqs;
mod secrets;
mod settings;
mod sidecar;
mod workspace;

use std::path::PathBuf;
use std::sync::Arc;

use serde_json::{json, Map, Value};
use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;

use sidecar::SidecarManager;

pub struct AppState {
    pub sidecar: Arc<SidecarManager>,
}

type CmdResult<T> = Result<T, String>;

fn err<E: std::fmt::Display>(e: E) -> String {
    e.to_string()
}

fn install_path(app: &AppHandle) -> CmdResult<PathBuf> {
    let s = settings::load_settings(app).map_err(err)?;
    s.install_path
        .map(PathBuf::from)
        .ok_or_else(|| "install path not set".to_string())
}

// ===== prerequisites & setup ==============================================
#[tauri::command]
fn check_prereqs() -> prereqs::PrereqStatus {
    prereqs::check()
}

#[tauri::command]
async fn install_prereqs() -> CmdResult<String> {
    tauri::async_runtime::spawn_blocking(prereqs::install_prereqs)
        .await
        .map_err(err)
}

#[tauri::command]
fn default_install_folder() -> String {
    settings::default_install_folder().to_string_lossy().to_string()
}

#[tauri::command]
async fn pick_install_folder(app: AppHandle) -> CmdResult<Option<String>> {
    let folder = app.dialog().file().blocking_pick_folder();
    Ok(folder.map(|p| p.to_string()))
}

#[tauri::command]
fn set_install_path(app: AppHandle, path: String) -> CmdResult<Value> {
    settings::set_install_path(&app, &path).map_err(err)?;
    Ok(settings::settings_json(&app))
}

#[tauri::command]
async fn setup_template(app: AppHandle) -> CmdResult<Value> {
    let install = install_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || workspace::setup_template(&install))
        .await
        .map_err(err)?
        .map_err(err)
}

#[tauri::command]
fn get_setup_status(app: AppHandle) -> Value {
    let masked = secrets::masked_api_key().ok().flatten();
    let s = settings::load_settings(&app).unwrap_or_default();
    let template_ready = s
        .install_path
        .as_ref()
        .map(|p| PathBuf::from(p).join("template").join(".git").exists())
        .unwrap_or(false);
    json!({
        "hasApiKey": masked.is_some(),
        "maskedApiKey": masked,
        "templateReady": template_ready,
        "installPath": s.install_path,
        "activeProject": s.active_project,
    })
}

// ===== secrets ============================================================
#[tauri::command]
fn set_api_key(key: String) -> CmdResult<()> {
    secrets::save_api_key(&key).map_err(err)
}

#[tauri::command]
fn clear_api_key() -> CmdResult<()> {
    secrets::delete_api_key().map_err(err)
}

#[tauri::command]
fn stored_api_key_masked() -> CmdResult<Option<String>> {
    secrets::masked_api_key().map_err(err)
}

// ===== settings & models ==================================================
#[tauri::command]
fn get_settings(app: AppHandle) -> Value {
    settings::settings_json(&app)
}

#[tauri::command]
fn set_models(app: AppHandle, project_id: String, models: Map<String, Value>) -> CmdResult<()> {
    let install = install_path(&app)?;
    workspace::set_models(&install, &project_id, models).map_err(err)
}

#[tauri::command]
async fn list_models(app: AppHandle, state: State<'_, AppState>) -> CmdResult<Value> {
    ensure_configured(&app, &state, None).await?;
    rpc(app, state.sidecar.clone(), "list_models", json!({})).await
}

// ===== projects ===========================================================
#[tauri::command]
fn suggest_project_id(app: AppHandle, display_name: String) -> CmdResult<String> {
    let install = install_path(&app)?;
    Ok(workspace::suggest_project_id(&install, &display_name))
}

#[tauri::command]
fn list_projects(app: AppHandle) -> CmdResult<Vec<Value>> {
    let install = install_path(&app)?;
    Ok(workspace::list_projects(&install))
}

#[tauri::command]
async fn create_project(app: AppHandle, id: String, display_name: String) -> CmdResult<String> {
    let install = install_path(&app)?;
    let project = tauri::async_runtime::spawn_blocking(move || {
        workspace::create_project(&install, &id, &display_name)
    })
    .await
    .map_err(err)?
    .map_err(err)?;
    Ok(project.to_string_lossy().to_string())
}

#[tauri::command]
fn rename_project(app: AppHandle, id: String, display_name: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    workspace::rename_project(&install, &id, &display_name).map_err(err)
}

#[tauri::command]
fn delete_project(app: AppHandle, id: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    workspace::delete_project(&install, &id).map_err(err)
}

#[tauri::command]
fn set_active_project(app: AppHandle, id: Option<String>) -> CmdResult<Value> {
    settings::set_active_project(&app, id.as_deref()).map_err(err)?;
    Ok(settings::settings_json(&app))
}

#[tauri::command]
fn set_project_type(app: AppHandle, id: String, project_type: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    workspace::set_project_type(&install, &id, &project_type).map_err(err)
}

// ===== target repos =======================================================
#[tauri::command]
fn add_repo(app: AppHandle, id: String, name: String, url: String, branch: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    workspace::add_repo(&install, &id, &name, &url, &branch).map_err(err)
}

#[tauri::command]
fn remove_repo(app: AppHandle, id: String, name: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    workspace::remove_repo(&install, &id, &name).map_err(err)
}

#[tauri::command]
async fn clone_repos(app: AppHandle, id: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || workspace::clone_repos(&install, &id))
        .await
        .map_err(err)?
        .map_err(err)
}

#[tauri::command]
fn list_repos(app: AppHandle, id: String) -> CmdResult<Vec<Value>> {
    let install = install_path(&app)?;
    workspace::list_repos(&install, &id).map_err(err)
}

#[tauri::command]
async fn pull_repos(app: AppHandle, id: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || workspace::pull_repos(&install, &id))
        .await
        .map_err(err)?
        .map_err(err)
}

// ===== meta docs ==========================================================
#[tauri::command]
fn upload_meta_files(app: AppHandle, id: String, sources: Vec<String>) -> CmdResult<Vec<String>> {
    let install = install_path(&app)?;
    workspace::upload_meta_files(&install, &id, &sources).map_err(err)
}

#[tauri::command]
fn list_meta_dir(app: AppHandle, id: String, rel_path: Option<String>) -> CmdResult<Vec<workspace::MetaEntry>> {
    let install = install_path(&app)?;
    workspace::list_meta_dir(&install, &id, rel_path.as_deref().unwrap_or("")).map_err(err)
}

#[tauri::command]
fn delete_meta_entry(app: AppHandle, id: String, path: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    workspace::delete_meta_entry(&install, &id, &path).map_err(err)
}

#[tauri::command]
fn get_chatlog(app: AppHandle, id: String) -> CmdResult<Vec<workspace::ChatMessage>> {
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    workspace::touch_project(&project);
    Ok(workspace::load_chatlog(&project))
}

#[tauri::command]
fn list_meta_files(app: AppHandle, id: String) -> CmdResult<Vec<Value>> {
    let install = install_path(&app)?;
    Ok(workspace::list_meta_files(&install, &id))
}

#[tauri::command]
fn delete_meta_file(app: AppHandle, id: String, name: String) -> CmdResult<()> {
    let install = install_path(&app)?;
    workspace::delete_meta_file(&install, &id, &name).map_err(err)
}

// ===== modules ============================================================
#[tauri::command]
fn list_modules(app: AppHandle, id: String) -> CmdResult<Vec<Value>> {
    let install = install_path(&app)?;
    Ok(workspace::list_modules(&install, &id))
}

#[tauri::command]
fn get_module_suggestions(app: AppHandle, id: String) -> CmdResult<Vec<String>> {
    let install = install_path(&app)?;
    Ok(workspace::get_module_suggestions(&install, &id))
}

// ===== logs / runs / artifacts ============================================
#[tauri::command]
fn get_event_log(app: AppHandle, id: String) -> CmdResult<String> {
    let install = install_path(&app)?;
    Ok(workspace::get_event_log(&install, &id))
}

#[tauri::command]
fn get_run_log(app: AppHandle, id: String, run_id: String) -> CmdResult<String> {
    let install = install_path(&app)?;
    Ok(workspace::get_run_log(&install, &id, &run_id))
}

#[tauri::command]
fn list_runs(app: AppHandle, id: String) -> CmdResult<Vec<Value>> {
    let install = install_path(&app)?;
    Ok(workspace::list_runs(&install, &id))
}

#[tauri::command]
fn get_run_artifacts(app: AppHandle, id: String, run_id: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    Ok(workspace::get_run_artifacts(&install, &id, &run_id))
}

#[tauri::command]
async fn get_run_state(app: AppHandle, state: State<'_, AppState>, id: String, run_id: String) -> CmdResult<Value> {
    let workspace = project_workspace(&app, &id)?;
    rpc(app, state.sidecar.clone(), "get_run_state",
        json!({"workspace": workspace, "runId": run_id})).await
}

// ===== module versioning ==================================================
#[tauri::command]
async fn get_modules_version_status(app: AppHandle, id: String, fetch: bool) -> CmdResult<Value> {
    let install = install_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        workspace::get_modules_version_status(&install, &id, fetch)
    })
    .await
    .map_err(err)?
    .map_err(err)
}

#[tauri::command]
async fn pull_project_modules(app: AppHandle, id: String) -> CmdResult<Value> {
    let install = install_path(&app)?;
    tauri::async_runtime::spawn_blocking(move || workspace::pull_project_modules(&install, &id))
        .await
        .map_err(err)?
        .map_err(err)
}

// ===== sidecar-backed operations ==========================================
fn project_workspace(app: &AppHandle, id: &str) -> CmdResult<String> {
    let install = install_path(app)?;
    Ok(workspace::project_dir(&install, id).to_string_lossy().to_string())
}

async fn rpc(
    app: AppHandle,
    mgr: Arc<SidecarManager>,
    method: &str,
    params: Value,
) -> CmdResult<Value> {
    let method = method.to_string();
    tauri::async_runtime::spawn_blocking(move || {
        mgr.request(&app, &method, params).map_err(|e| e.to_string())
    })
    .await
    .map_err(err)?
}

/// Configure-before-use: send the keychain key + project models to the sidecar.
async fn ensure_configured(
    app: &AppHandle,
    state: &State<'_, AppState>,
    project_id: Option<&str>,
) -> CmdResult<()> {
    let key = secrets::load_api_key().map_err(err)?.unwrap_or_default();
    if key.is_empty() {
        return Err("no API key configured".into());
    }
    let mut models = Map::new();
    if let Some(pid) = project_id {
        let install = install_path(app)?;
        if let Ok(meta) = workspace::read_meta(&workspace::project_dir(&install, pid)) {
            models = meta.models;
        }
    }
    rpc(app.clone(), state.sidecar.clone(), "configure",
        json!({"api_key": key, "models": models})).await?;
    Ok(())
}

#[tauri::command]
async fn configure_sidecar(app: AppHandle, state: State<'_, AppState>, project_id: Option<String>) -> CmdResult<()> {
    ensure_configured(&app, &state, project_id.as_deref()).await
}

#[tauri::command]
async fn sidecar_health(app: AppHandle, state: State<'_, AppState>) -> CmdResult<Value> {
    ensure_configured(&app, &state, None).await?;
    rpc(app, state.sidecar.clone(), "health", json!({})).await
}

#[tauri::command]
async fn send_chat(app: AppHandle, state: State<'_, AppState>, id: String, message: String) -> CmdResult<Value> {
    ensure_configured(&app, &state, Some(&id)).await?;
    let install = install_path(&app)?;
    let project = workspace::project_dir(&install, &id);
    workspace::touch_project(&project);
    workspace::append_chatlog(&project, "user", &message).map_err(err)?;
    let workspace = project.to_string_lossy().to_string();
    let result = rpc(app, state.sidecar.clone(), "chat", json!({"workspace": workspace, "message": message})).await?;
    if let Some(reply) = result.get("reply").and_then(|v| v.as_str()) {
        workspace::append_chatlog(&project, "assistant", reply).map_err(err)?;
    }
    Ok(result)
}

#[allow(clippy::too_many_arguments)]
#[tauri::command]
async fn start_run(
    app: AppHandle,
    state: State<'_, AppState>,
    id: String,
    run_id: String,
    modules: Vec<String>,
    force: bool,
    resume: bool,
    specific_instructions: Option<String>,
) -> CmdResult<Value> {
    ensure_configured(&app, &state, Some(&id)).await?;
    let workspace = project_workspace(&app, &id)?;
    let install = install_path(&app)?;
    let meta = workspace::read_meta(&workspace::project_dir(&install, &id)).map_err(err)?;
    rpc(
        app,
        state.sidecar.clone(),
        "run_modules",
        json!({
            "workspace": workspace,
            "runId": run_id,
            "modules": modules,
            "projectType": meta.project_type,
            "force": force,
            "resume": resume,
            "specific_instructions": specific_instructions,
        }),
    )
    .await
}

#[tauri::command]
fn stop_run(state: State<'_, AppState>) -> CmdResult<()> {
    state.sidecar.cancel();
    Ok(())
}

#[tauri::command]
async fn preview_file(app: AppHandle, state: State<'_, AppState>, id: String, path: String, base: Option<String>) -> CmdResult<Value> {
    let workspace = project_workspace(&app, &id)?;
    rpc(app, state.sidecar.clone(), "preview",
        json!({"workspace": workspace, "path": path, "base": base.unwrap_or_else(|| "repos".into())})).await
}

/// Generate a runId: timestamp + short random (spec 9.1).
#[tauri::command]
fn new_run_id() -> String {
    use rand::Rng;
    let ts = chrono::Local::now().format("%Y%m%d-%H%M%S");
    let chars: Vec<char> = "0123456789abcdef".chars().collect();
    let mut rng = rand::thread_rng();
    let suffix: String = (0..4).map(|_| chars[rng.gen_range(0..chars.len())]).collect();
    format!("{ts}-{suffix}")
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let sidecar_dir = sidecar_dir();
    let state = AppState {
        sidecar: Arc::new(SidecarManager::new(sidecar_dir)),
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .manage(state)
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<AppState>() {
                    state.sidecar.stop();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            check_prereqs,
            install_prereqs,
            get_setup_status,
            default_install_folder,
            pick_install_folder,
            set_install_path,
            setup_template,
            set_api_key,
            clear_api_key,
            stored_api_key_masked,
            get_settings,
            set_models,
            list_models,
            suggest_project_id,
            list_projects,
            create_project,
            rename_project,
            delete_project,
            set_active_project,
            set_project_type,
            add_repo,
            remove_repo,
            clone_repos,
            list_repos,
            pull_repos,
            upload_meta_files,
            list_meta_dir,
            list_meta_files,
            delete_meta_entry,
            delete_meta_file,
            get_chatlog,
            list_modules,
            get_module_suggestions,
            get_event_log,
            get_run_log,
            get_run_state,
            list_runs,
            get_run_artifacts,
            configure_sidecar,
            sidecar_health,
            send_chat,
            start_run,
            stop_run,
            new_run_id,
            pull_project_modules,
            get_modules_version_status,
            preview_file
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// In dev, the sidecar lives next to the app at ../sidecar; in release the frozen
/// binary sits next to the exe (handled inside SidecarManager).
fn sidecar_dir() -> PathBuf {
    if cfg!(debug_assertions) {
        // CARGO_MANIFEST_DIR = app/src-tauri ; sidecar is app/sidecar
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        manifest.parent().map(|p| p.join("sidecar")).unwrap_or_else(|| PathBuf::from("sidecar"))
    } else {
        std::env::current_exe()
            .ok()
            .and_then(|e| e.parent().map(PathBuf::from))
            .unwrap_or_else(|| PathBuf::from("."))
    }
}
