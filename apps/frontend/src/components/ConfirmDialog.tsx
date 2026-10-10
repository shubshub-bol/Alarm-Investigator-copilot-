import { useEffect, useRef } from "react";

interface Props {
  title: string;
  message: string;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/** A modal yes/no question. The native <dialog> keeps focus inside and turns Esc into a cancel. */
export function ConfirmDialog({ title, message, confirmLabel, onConfirm, onCancel }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    if (typeof el.showModal === "function") {
      if (!el.open) el.showModal();
    } else {
      el.setAttribute("open", ""); // jsdom has no showModal
    }
    return () => {
      if (el.open && typeof el.close === "function") el.close();
    };
  }, []);

  return (
    <dialog
      ref={dialog}
      className="confirm"
      aria-labelledby="confirm-title"
      aria-describedby="confirm-message"
      onCancel={(event) => {
        event.preventDefault(); // the parent closes it by unmounting
        onCancel();
      }}
    >
      <h2 id="confirm-title">{title}</h2>
      <p id="confirm-message">{message}</p>
      <div className="confirm-actions">
        <button type="button" className="button" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="button button-danger" onClick={onConfirm}>
          {confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
