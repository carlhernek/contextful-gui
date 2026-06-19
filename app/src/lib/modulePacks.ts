import type { ModuleInfo } from "./ipc";

export const PACKS = [
  "Core",
  "Engineering",
  "Sales & Growth",
  "Onboarding & Docs",
  "Compliance & Risk",
] as const;

export function packFullySelected(
  pack: string,
  modules: ModuleInfo[],
  selected: string[],
): boolean {
  const packIds = modules.filter((m) => m.packs.includes(pack)).map((m) => m.id);
  return packIds.length > 0 && packIds.every((id) => selected.includes(id));
}
