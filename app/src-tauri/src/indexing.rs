//! Workspace index annotations and index file access.

use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use chrono::Local;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub const INDEX_FILE: &str = ".workspace-index.json";
pub const ANNOTATIONS_FILE: &str = ".index-annotations.json";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IndexAnnotation {
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub keywords: Vec<String>,
    #[serde(default)]
    pub updated_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct AnnotationsDoc {
    #[serde(default)]
    version: u32,
    #[serde(default)]
    items: serde_json::Map<String, Value>,
}

fn empty_index() -> Value {
    json!({
        "version": 1,
        "updatedAt": null,
        "project": {},
        "items": []
    })
}

fn load_annotations(project: &Path) -> Result<AnnotationsDoc> {
    let path = project.join(ANNOTATIONS_FILE);
    if !path.exists() {
        return Ok(AnnotationsDoc {
            version: 1,
            items: serde_json::Map::new(),
        });
    }
    let raw = fs::read_to_string(&path).with_context(|| format!("read {ANNOTATIONS_FILE}"))?;
    let doc: AnnotationsDoc = serde_json::from_str(&raw).unwrap_or(AnnotationsDoc {
        version: 1,
        items: serde_json::Map::new(),
    });
    Ok(doc)
}

fn save_annotations(project: &Path, doc: &AnnotationsDoc) -> Result<()> {
    let path = project.join(ANNOTATIONS_FILE);
    fs::write(&path, serde_json::to_string_pretty(doc)?).context("write annotations")
}

pub fn set_annotation(
    project: &Path,
    item_id: &str,
    description: &str,
    keywords: &[String],
) -> Result<IndexAnnotation> {
    let mut doc = load_annotations(project)?;
    let ann = IndexAnnotation {
        description: description.to_string(),
        keywords: keywords.to_vec(),
        updated_at: Some(Local::now().to_rfc3339()),
    };
    doc.items.insert(
        item_id.to_string(),
        serde_json::to_value(&ann).context("serialize annotation")?,
    );
    save_annotations(project, &doc)?;
    Ok(ann)
}

pub fn get_index(project: &Path) -> Result<Value> {
    let path = project.join(INDEX_FILE);
    if !path.exists() {
        return Ok(empty_index());
    }
    let raw = fs::read_to_string(&path).with_context(|| format!("read {INDEX_FILE}"))?;
    Ok(serde_json::from_str(&raw).unwrap_or_else(|_| empty_index()))
}

pub fn read_index_item(project: &Path, item_id: &str) -> Result<Option<Value>> {
    let index = get_index(project)?;
    let items = index
        .get("items")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    Ok(items.into_iter().find(|it| it.get("id").and_then(|v| v.as_str()) == Some(item_id)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::{write_meta, ProjectMeta};
    use tempfile::tempdir;

    fn fixture_project() -> (tempfile::TempDir, PathBuf) {
        let tmp = tempdir().unwrap();
        let project = tmp.path().join("projects").join("demo");
        fs::create_dir_all(&project).unwrap();
        write_meta(
            &project,
            &ProjectMeta {
                display_name: "Demo".to_string(),
                project_type: "both".to_string(),
                repos: vec![],
                models: serde_json::Map::new(),
                deleted: false,
            },
        )
        .unwrap();
        (tmp, project)
    }

    #[test]
    fn set_annotation_roundtrip() {
        let (_tmp, project) = fixture_project();
        let ann = set_annotation(
            &project,
            "repo:backoffice",
            "Admin API service",
            &["backoffice".into(), "orders".into()],
        )
        .unwrap();
        assert_eq!(ann.description, "Admin API service");
        assert_eq!(ann.keywords.len(), 2);

        let doc = load_annotations(&project).unwrap();
        assert!(doc.items.contains_key("repo:backoffice"));
    }

    #[test]
    fn get_index_missing_returns_empty() {
        let (_tmp, project) = fixture_project();
        let index = get_index(&project).unwrap();
        assert_eq!(index["items"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn annotations_survive_index_overwrite() {
        let (_tmp, project) = fixture_project();
        set_annotation(&project, "meta:req.md", "Requirements doc", &["req".into()]).unwrap();
        fs::write(
            project.join(INDEX_FILE),
            r#"{"version":1,"items":[{"id":"meta:req.md","description":"auto"}]}"#,
        )
        .unwrap();
        let doc = load_annotations(&project).unwrap();
        assert!(doc.items.contains_key("meta:req.md"));
    }
}
