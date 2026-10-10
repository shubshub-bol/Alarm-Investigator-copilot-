import { useEffect, useRef, useState } from "react";
import type { ConversationSummary } from "../api/types";
import { ConfirmDialog } from "./ConfirmDialog";
import { MoreIcon } from "./icons";

interface Props {
  items: ConversationSummary[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

/** Saved chats. Each has a "⋯" menu: Rename edits the title in place, Delete asks first. */
export function ChatList({ items, activeId, onOpen, onRename, onDelete }: Props) {
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<ConversationSummary | null>(null);
  const [failure, setFailure] = useState<{ id: string; message: string } | null>(null);

  async function attempt(id: string, verb: string, action: () => Promise<void>) {
    setFailure(null);
    try {
      await action();
    } catch (error) {
      setFailure({ id, message: `Couldn't ${verb} this chat. ${(error as Error).message}` });
    }
  }

  return (
    <>
      <ul className="chat-list">
        {items.map((chat) => {
          const id = chat.conversation_id;
          const active = id === activeId;
          return (
            <li key={id} className="chat-item" data-active={active}>
              {editing === id ? (
                <RenameField
                  title={chat.title}
                  onSave={(title) => {
                    setEditing(null);
                    void attempt(id, "rename", () => onRename(id, title));
                  }}
                  onCancel={() => setEditing(null)}
                />
              ) : (
                <button
                  type="button"
                  className="chat-open"
                  title={chat.title}
                  aria-current={active ? "page" : undefined}
                  onClick={() => onOpen(id)}
                >
                  {chat.title}
                </button>
              )}
              <ChatMenu
                title={chat.title}
                open={menuFor === id}
                onToggle={(open) => setMenuFor(open ? id : null)}
                onRename={() => {
                  setMenuFor(null);
                  setEditing(id);
                }}
                onDelete={() => {
                  setMenuFor(null);
                  setConfirming(chat);
                }}
              />
              {failure?.id === id && (
                <p className="chat-error" role="alert">
                  {failure.message}
                </p>
              )}
            </li>
          );
        })}
      </ul>
      {confirming && (
        <ConfirmDialog
          title="Delete chat?"
          message={`This deletes “${confirming.title}” and its answers.`}
          confirmLabel="Delete"
          onCancel={() => setConfirming(null)}
          onConfirm={() => {
            const id = confirming.conversation_id;
            setConfirming(null);
            void attempt(id, "delete", () => onDelete(id));
          }}
        />
      )}
    </>
  );
}

interface MenuProps {
  title: string;
  open: boolean;
  onToggle: (open: boolean) => void;
  onRename: () => void;
  onDelete: () => void;
}

function ChatMenu({ title, open, onToggle, onRename, onDelete }: MenuProps) {
  const box = useRef<HTMLDivElement>(null);

  // Like any popup menu: a click elsewhere or Esc closes it.
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: PointerEvent) => {
      if (!box.current?.contains(event.target as Node)) onToggle(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onToggle(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onToggle]);

  return (
    <div className="chat-menu-box" ref={box}>
      <button
        type="button"
        className="icon-button chat-more"
        aria-label={`Options for ${title}`}
        aria-expanded={open}
        onClick={() => onToggle(!open)}
      >
        <MoreIcon />
      </button>
      {open && (
        <div className="chat-menu">
          <button type="button" onClick={onRename}>
            Rename
          </button>
          <button type="button" onClick={onDelete}>
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

function RenameField({ title, onSave, onCancel }: { title: string; onSave: (title: string) => void; onCancel: () => void }) {
  const [value, setValue] = useState(title);
  const input = useRef<HTMLInputElement>(null);
  const finished = useRef(false);

  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);

  function finish(save: boolean) {
    if (finished.current) return; // Enter followed by blur must not save twice
    finished.current = true;
    const next = value.trim();
    if (save && next && next !== title) onSave(next);
    else onCancel();
  }

  return (
    <input
      ref={input}
      className="chat-rename"
      aria-label="Chat name"
      value={value}
      maxLength={80}
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") finish(true);
        else if (event.key === "Escape") finish(false);
      }}
      onBlur={() => finish(true)}
    />
  );
}
