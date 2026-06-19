import type { TasksDoc } from "./ipc";

export function tasksToJson(doc: TasksDoc): string {
  return JSON.stringify(doc, null, 2);
}

export function tasksToMarkdown(doc: TasksDoc): string {
  const lines: string[] = [
    `# Tasks — ${doc.moduleId}`,
    "",
    `Run: \`${doc.runId}\``,
    "",
  ];

  for (const t of doc.tasks) {
    lines.push(`## ${t.id} (${t.priority} · ${t.effort})`);
    lines.push("");
    lines.push(`**${t.title}**`);
    lines.push("");
    lines.push(t.rationale);
    lines.push("");
    if (t.evidence.length > 0) {
      lines.push("### Evidence");
      for (const e of t.evidence) {
        lines.push(`- \`${e}\``);
      }
      lines.push("");
    }
    if (t.agentic_spec?.trim()) {
      lines.push("### Agentic spec");
      lines.push("");
      lines.push("```");
      lines.push(t.agentic_spec.trim());
      lines.push("```");
      lines.push("");
    }
  }

  return lines.join("\n").trimEnd() + "\n";
}

export function downloadText(filename: string, content: string, mime: string): void {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function tasksFilename(moduleId: string, ext: "md" | "json"): string {
  return `${moduleId}-tasks.${ext}`;
}
