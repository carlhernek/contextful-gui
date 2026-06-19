import type { Task } from "./ipc";

/**
 * Build a self-contained prompt an engineer can paste into a coding agent.
 * Composes task fields even when agentic_spec alone is terse or step-list shaped.
 */
export function formatTaskPrompt(task: Task, moduleId?: string): string {
  const lines: string[] = [
    "You are a coding agent working in a Contextful project workspace.",
  ];

  if (moduleId) {
    lines.push(`Context: follow-up from the "${moduleId}" analysis module (task ${task.id}).`);
  } else {
    lines.push(`Context: follow-up task ${task.id}.`);
  }

  lines.push("", `## Objective`, task.title, "", `## Background`, task.rationale.trim());

  if (task.evidence.length > 0) {
    lines.push("", "## Files to inspect (start here)");
    for (const path of task.evidence) {
      lines.push(`- ${path}`);
    }
  }

  const spec = task.agentic_spec?.trim();
  if (spec) {
    lines.push("", "## What to do", spec);
  }

  lines.push(
    "",
    "## Done when",
    `- ${task.title} is fully addressed.`,
    "- Changes stay scoped to this task — no unrelated refactors.",
    "- You verify the fix (run relevant tests or describe manual validation).",
  );

  return lines.join("\n").trimEnd() + "\n";
}
