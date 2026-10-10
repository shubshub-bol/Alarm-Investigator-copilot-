import { useEffect, useRef, useState } from "react";
import type { ConversationSummary } from "../api/types";
import type { ListStatus } from "../state/useConversations";
import { ChatList } from "./ChatList";
import { NewChatIcon, SearchIcon, SidebarIcon } from "./icons";

export interface ChatsView {
  items: ConversationSummary[];
  status: ListStatus;
  error: string | null;
  refresh: () => Promise<void>;
  rename: (id: string, title: string) => Promise<void>;
}

interface Props {
  chats: ChatsView;
  activeId: string | null;
  collapsed: boolean; // wide screens: a thin strip of icons
  open: boolean; // small screens: shown over the page
  onCollapse: (collapsed: boolean) => void;
  onClose: () => void;
  onNew: () => void;
  onOpen: (id: string) => void;
  onDelete: (id: string) => Promise<void>;
}

/** New chat, search and the saved chats, like ChatGPT's sidebar. */
export function Sidebar({ chats, activeId, collapsed, open, onCollapse, onClose, onNew, onOpen, onDelete }: Props) {
  const [query, setQuery] = useState("");
  const [focusSearch, setFocusSearch] = useState(false);
  const search = useRef<HTMLInputElement>(null);
  const needle = query.trim().toLowerCase();
  const shown = needle ? chats.items.filter((c) => c.title.toLowerCase().includes(needle)) : chats.items;

  // The search icon on the collapsed strip expands the sidebar and puts the cursor in the search box.
  useEffect(() => {
    if (focusSearch && search.current) {
      search.current.focus();
      setFocusSearch(false);
    }
  }, [focusSearch, collapsed]);

  if (collapsed && !open) {
    return (
      <nav className="sidebar sidebar-rail" aria-label="Chats">
        <button type="button" className="icon-button" aria-label="Open sidebar" onClick={() => onCollapse(false)}>
          <SidebarIcon />
        </button>
        <button type="button" className="icon-button" aria-label="New chat" onClick={onNew}>
          <NewChatIcon />
        </button>
        <button
          type="button"
          className="icon-button"
          aria-label="Search chats"
          onClick={() => {
            setFocusSearch(true);
            onCollapse(false);
          }}
        >
          <SearchIcon />
        </button>
      </nav>
    );
  }

  return (
    <>
      {open && <div className="sidebar-backdrop" onClick={onClose} aria-hidden="true" />}
      <nav className="sidebar" aria-label="Chats" data-open={open}>
        <div className="sidebar-head">
          <button
            type="button"
            className="icon-button"
            aria-label="Close sidebar"
            onClick={() => (open ? onClose() : onCollapse(true))}
          >
            <SidebarIcon />
          </button>
        </div>
        <button type="button" className="sidebar-action" onClick={onNew}>
          <NewChatIcon />
          New chat
        </button>
        <label className="sidebar-search">
          <SearchIcon />
          <span className="visually-hidden">Search chats</span>
          <input
            ref={search}
            type="search"
            placeholder="Search chats"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <h2 className="sidebar-label">Chats</h2>
        <ListNote chats={chats} searching={needle.length > 0} matches={shown.length} />
        <ChatList items={shown} activeId={activeId} onOpen={onOpen} onRename={chats.rename} onDelete={onDelete} />
      </nav>
    </>
  );
}

function ListNote({ chats, searching, matches }: { chats: ChatsView; searching: boolean; matches: number }) {
  if (chats.status === "error") {
    return (
      <p className="sidebar-note" role="alert">
        Couldn't load chats.{" "}
        <button type="button" className="link" onClick={() => void chats.refresh()}>
          Retry
        </button>
      </p>
    );
  }
  if (chats.status === "loading" && chats.items.length === 0) return <p className="sidebar-note">Loading chats…</p>;
  if (chats.items.length === 0) return <p className="sidebar-note">No chats yet.</p>;
  if (searching && matches === 0) return <p className="sidebar-note">No chats match.</p>;
  return null;
}
