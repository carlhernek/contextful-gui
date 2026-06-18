//! Central job registry: single-flight long-running work with app-wide busy state.

use std::cell::Cell;
use std::sync::{Arc, Mutex};

use chrono::Local;
use serde::Serialize;
use tauri::{AppHandle, Emitter, Runtime};

use crate::sidecar::{SidecarEvent, EVENT_NAME};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum JobKind {
    Run,
    Index,
    Clone,
    Pull,
}

impl JobKind {
    pub fn as_str(self) -> &'static str {
        match self {
            JobKind::Run => "run",
            JobKind::Index => "index",
            JobKind::Clone => "clone",
            JobKind::Pull => "pull",
        }
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Job {
    pub key: String,
    pub kind: JobKind,
    pub project_id: String,
    pub label: String,
    pub started_at: String,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct JobBusy {
    pub current: Job,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct JobEvent {
    action: String,
    job: Job,
}

pub struct JobManager {
    current: Mutex<Option<Job>>,
}

impl JobManager {
    pub fn new() -> Self {
        Self {
            current: Mutex::new(None),
        }
    }

    pub fn snapshot(&self) -> Vec<Job> {
        self.current
            .lock()
            .unwrap()
            .clone()
            .map(|j| vec![j])
            .unwrap_or_default()
    }

    pub fn current(&self) -> Option<Job> {
        self.current.lock().unwrap().clone()
    }

    /// Begin a job or reject if another is active (user-initiated operations).
    pub fn try_begin<R: Runtime>(
        self: &Arc<Self>,
        app: &AppHandle<R>,
        kind: JobKind,
        project_id: &str,
        label: &str,
    ) -> Result<JobGuard<R>, JobBusy> {
        self.try_begin_inner(app, kind, project_id, label, false)
    }

    /// Begin a job or return None if another is active (auto/coalesced operations).
    pub fn try_begin_or_skip<R: Runtime>(
        self: &Arc<Self>,
        app: &AppHandle<R>,
        kind: JobKind,
        project_id: &str,
        label: &str,
    ) -> Option<JobGuard<R>> {
        match self.try_begin_inner(app, kind, project_id, label, true) {
            Ok(guard) => Some(guard),
            Err(_) => None,
        }
    }

    fn try_begin_inner<R: Runtime>(
        self: &Arc<Self>,
        app: &AppHandle<R>,
        kind: JobKind,
        project_id: &str,
        label: &str,
        skip_if_busy: bool,
    ) -> Result<JobGuard<R>, JobBusy> {
        let mut slot = self.current.lock().unwrap();
        if let Some(current) = slot.clone() {
            if skip_if_busy {
                return Err(JobBusy { current }); // caller treats as skip via try_begin_or_skip
            }
            return Err(JobBusy { current });
        }

        let job = Job {
            key: format!("{}:{}", kind.as_str(), project_id),
            kind,
            project_id: project_id.to_string(),
            label: label.to_string(),
            started_at: Local::now().to_rfc3339(),
        };
        *slot = Some(job.clone());
        drop(slot);

        emit_job_event(app, "started", &job);
        Ok(JobGuard {
            manager: Arc::clone(self),
            app: app.clone(),
            failed: Cell::new(false),
        })
    }

    fn finish<R: Runtime>(&self, app: &AppHandle<R>, failed: bool) {
        let job = self.current.lock().unwrap().take();
        if let Some(job) = job {
            emit_job_event(app, if failed { "failed" } else { "finished" }, &job);
        }
    }
}

pub struct JobGuard<R: Runtime> {
    manager: Arc<JobManager>,
    app: AppHandle<R>,
    failed: Cell<bool>,
}

impl<R: Runtime> JobGuard<R> {
    pub fn fail(&self) {
        self.failed.set(true);
    }
}

impl<R: Runtime> Drop for JobGuard<R> {
    fn drop(&mut self) {
        self.manager.finish(&self.app, self.failed.get());
    }
}

fn emit_job_event<R: Runtime>(app: &AppHandle<R>, action: &str, job: &Job) {
    let _ = app.emit(
        EVENT_NAME,
        SidecarEvent {
            id: None,
            event: "job".to_string(),
            data: serde_json::to_value(JobEvent {
                action: action.to_string(),
                job: job.clone(),
            })
            .unwrap_or_default(),
        },
    );
}

pub fn busy_error(busy: JobBusy) -> String {
    format!(
        "Contextful is busy with {} ({})",
        busy.current.label,
        busy.current.kind.as_str()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_empty_by_default() {
        let mgr = JobManager::new();
        assert!(mgr.snapshot().is_empty());
    }

    #[test]
    fn slot_blocks_while_held() {
        let mgr = Arc::new(JobManager::new());
        {
            let mut slot = mgr.current.lock().unwrap();
            *slot = Some(Job {
                key: "run:p1".into(),
                kind: JobKind::Run,
                project_id: "p1".into(),
                label: "Running".into(),
                started_at: "now".into(),
            });
        }
        assert!(mgr.current().is_some());
        assert_eq!(mgr.snapshot().len(), 1);
        {
            let mut slot = mgr.current.lock().unwrap();
            *slot = None;
        }
        assert!(mgr.snapshot().is_empty());
    }

    #[test]
    fn busy_error_message() {
        let msg = busy_error(JobBusy {
            current: Job {
                key: "run:p1".into(),
                kind: JobKind::Run,
                project_id: "p1".into(),
                label: "Running pipeline".into(),
                started_at: "now".into(),
            },
        });
        assert!(msg.contains("Running pipeline"));
        assert!(msg.contains("run"));
    }
}
