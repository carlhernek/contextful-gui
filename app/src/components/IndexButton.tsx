import { useState } from "react";
import { IndexItemModal } from "./IndexItemModal";

interface Props {
  projectId: string;
  itemId: string;
  disabled?: boolean;
  title?: string;
}

export function IndexButton({ projectId, itemId, disabled, title = "Edit index entry" }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        className="rounded border border-cf-border px-1.5 py-0.5 text-xs text-cf-muted hover:bg-cf-surface hover:text-cf-ink"
        title={title}
        disabled={disabled}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
      >
        ⓘ
      </button>
      <IndexItemModal
        open={open}
        projectId={projectId}
        itemId={itemId}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
