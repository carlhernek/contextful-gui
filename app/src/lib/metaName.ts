/** Validate a meta file/folder name (basename only). Mirrors Rust rename_meta_entry rules. */
export function validateMetaName(name: string): string | null {
  const trimmed = name.trim().replace(/\\/g, "/");
  if (!trimmed) return "Name cannot be empty";
  if (trimmed.includes("/")) return "Name cannot contain /";
  if (trimmed.includes("..")) return "Invalid name";
  if (/[<>:"|?*]/.test(trimmed)) return "Name contains invalid characters";
  return null;
}
