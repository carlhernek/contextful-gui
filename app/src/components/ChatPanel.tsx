import { useEffect, useRef, useState } from "react";
import { api, onContextfulEvent, type ChatMessage } from "../lib/ipc";
import { Spinner } from "./Spinner";

interface Props {
  projectId: string;
  onRunIntent?: (modules: string[], force: boolean) => void;
}

export function ChatPanel({ projectId, onRunIntent }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void api.getChatlog(projectId).then(setMessages);
  }, [projectId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setBusy(true);
    setStreaming("");

    const userMsg: ChatMessage = {
      role: "user",
      content: text,
      ts: new Date().toISOString(),
    };
    setMessages((m) => [...m, userMsg]);

    let unlisten: (() => void) | undefined;
    try {
      unlisten = await onContextfulEvent((e) => {
        if (e.event === "token" && typeof e.data === "string") {
          setStreaming((s) => s + e.data);
        }
      });

      const res = await api.sendChat(projectId, text);

      if (res.type === "run_intent" && res.modules?.length && onRunIntent) {
        onRunIntent(res.modules, res.force ?? false);
      }

      setStreaming("");
      const updated = await api.getChatlog(projectId);
      setMessages(updated);
    } catch (e) {
      setStreaming("");
      setMessages((m) => [
        ...m,
        { role: "assistant", content: String(e), ts: new Date().toISOString() },
      ]);
    } finally {
      unlisten?.();
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex h-[calc(100vh-8rem)] max-w-3xl flex-col rounded-lg border border-cf-border bg-cf-surface">
      <div className="flex-1 overflow-y-auto p-4">
        {messages.length === 0 && !streaming && (
          <p className="text-sm text-cf-muted">
            Ask the orchestrator about your project, runs, or module results.
          </p>
        )}
        <div className="space-y-3">
          {messages.map((m, i) => (
            <div
              key={`${m.ts}-${i}`}
              className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                  m.role === "user"
                    ? "bg-cf-accent text-cf-accent-ink"
                    : "bg-cf-surface-2 text-cf-ink"
                }`}
              >
                {m.content}
              </div>
            </div>
          ))}
          {streaming && (
            <div className="flex justify-start">
              <div className="max-w-[85%] rounded-lg bg-cf-surface-2 px-3 py-2 text-sm whitespace-pre-wrap text-cf-ink">
                {streaming}
              </div>
            </div>
          )}
        </div>
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-cf-border p-3">
        <div className="flex gap-2">
          <textarea
            className="flex-1 resize-none rounded-md border border-cf-border bg-cf-surface-2 px-2 py-1.5 text-sm text-cf-ink outline-none focus:border-cf-accent"
            rows={2}
            placeholder="e.g. what did the security module find?"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            disabled={busy}
          />
          <button
            type="button"
            className="self-end rounded-md bg-cf-accent px-3 py-1.5 text-sm font-medium text-cf-accent-ink hover:opacity-90 disabled:opacity-40"
            onClick={() => void send()}
            disabled={busy || !input.trim()}
          >
            {busy ? <Spinner size={12} /> : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
