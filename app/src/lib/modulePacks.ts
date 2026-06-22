import type { ModuleInfo } from "./ipc";

export const PACKS = [
  "Core",
  "Engineering",
  "Sales & Growth",
  "Onboarding & Docs",
  "Compliance & Risk",
] as const;

export type PackName = (typeof PACKS)[number];

export function primaryPack(module: ModuleInfo): PackName {
  for (const pack of PACKS) {
    if (module.packs.includes(pack)) return pack;
  }
  return "Core";
}

export function secondaryPacks(module: ModuleInfo): string[] {
  const primary = primaryPack(module);
  return module.packs.filter((p) => p !== primary);
}

export function groupModulesByPrimaryPack(modules: ModuleInfo[]): Map<PackName, ModuleInfo[]> {
  const groups = new Map<PackName, ModuleInfo[]>();
  for (const pack of PACKS) {
    groups.set(pack, []);
  }
  const sorted = [...modules].sort((a, b) => {
    if (a.id === "workspace-index") return -1;
    if (b.id === "workspace-index") return 1;
    return a.title.localeCompare(b.title);
  });
  for (const m of sorted) {
    groups.get(primaryPack(m))!.push(m);
  }
  return groups;
}

export function packFullySelected(
  pack: string,
  modules: ModuleInfo[],
  selected: string[],
): boolean {
  const packIds = modules.filter((m) => m.packs.includes(pack)).map((m) => m.id);
  return packIds.length > 0 && packIds.every((id) => selected.includes(id));
}

export function packModuleIds(modules: ModuleInfo[], pack: string): string[] {
  return modules.filter((m) => m.packs.includes(pack)).map((m) => m.id);
}

export function sectionSelectionCount(
  packModules: ModuleInfo[],
  selected: string[],
): { selected: number; total: number } {
  const ids = packModules.map((m) => m.id);
  return {
    selected: ids.filter((id) => selected.includes(id)).length,
    total: ids.length,
  };
}
