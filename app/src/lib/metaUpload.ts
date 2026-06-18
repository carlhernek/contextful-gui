import type { DialogFilter } from "@tauri-apps/plugin-dialog";

/** Extensions allowed for meta document upload (must match Rust META_UPLOAD_EXTENSIONS). */
export const META_UPLOAD_EXTENSIONS = [
  "txt", "md", "markdown", "docx", "doc", "pdf", "rtf",
  "csv", "xlsx", "xls", "xlsm",
  "jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico",
  "json", "yaml", "yml", "xml", "toml", "ini", "cfg", "log",
  "html", "htm", "css", "py", "js", "ts", "tsx", "jsx", "rs", "go", "java", "sh", "sql",
] as const;

export const META_UPLOAD_FILTERS: DialogFilter[] = [
  {
    name: "Documents",
    extensions: ["txt", "md", "docx", "doc", "pdf", "rtf"],
  },
  {
    name: "Spreadsheets",
    extensions: ["csv", "xlsx", "xls"],
  },
  {
    name: "Images",
    extensions: ["jpg", "jpeg", "png", "gif", "webp", "svg", "bmp"],
  },
  {
    name: "Data & config",
    extensions: ["json", "yaml", "yml", "xml", "toml", "ini", "cfg"],
  },
  {
    name: "All supported",
    extensions: [...META_UPLOAD_EXTENSIONS],
  },
];
