interface Props {
  open: boolean;
  title: string;
  body: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmModal({
  open,
  title,
  body,
  confirmLabel = "Confirm",
  danger,
  onConfirm,
  onCancel,
}: Props) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-cf-bg/70">
      <div className="w-[420px] rounded-lg border border-cf-border bg-cf-surface p-5">
        <h3 className="mb-2 text-lg font-semibold text-cf-ink">{title}</h3>
        <p className="mb-5 text-sm text-cf-muted">{body}</p>
        <div className="flex justify-end gap-2">
          <button
            className="rounded-md border border-cf-border px-3 py-1.5 text-sm text-cf-ink hover:bg-cf-surface-2"
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${
              danger
                ? "bg-cf-danger text-white hover:opacity-90"
                : "bg-cf-accent text-cf-accent-ink hover:opacity-90"
            }`}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
