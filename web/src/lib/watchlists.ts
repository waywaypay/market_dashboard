/**
 * Custom watchlists — a user-defined subset of the active universe's tickers,
 * saved in this browser. Selecting one focuses the cockpit on those names.
 *
 * This is the one piece of state the UI owns: it is a personal lens over the
 * pipeline's artifact, not pipeline data, so it lives in localStorage — true to
 * the product's deliberately server-stateless design (no DB, no auth, no
 * multi-user). Watchlists are scoped per universe id, since a ticker only has
 * meaning inside the universe it belongs to.
 */
import { useCallback, useEffect, useState } from "react";

export type Watchlist = {
  id: string;
  name: string;
  tickers: string[];
};

// Bump the suffix if the stored shape ever changes incompatibly.
const STORE_KEY = "pmr.watchlists.v1"; // universeId -> Watchlist[]
const ACTIVE_KEY = "pmr.watchlist.active.v1"; // universeId -> active watchlist id

type Store = Record<string, Watchlist[]>;
type ActiveMap = Record<string, string | null>;

function readJSON<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback; // storage disabled (private mode) or malformed — degrade quietly
  }
}

function writeJSON(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage disabled or over quota — the in-memory state still works this session */
  }
}

function newId(): string {
  return `wl_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
}

/**
 * Watchlist state for one universe: the saved lists, the active selection
 * (null = "All names", the full universe), and CRUD that persists every change.
 * Re-keys itself when the universe changes and syncs across browser tabs.
 */
export function useWatchlists(universeId: string) {
  const [lists, setLists] = useState<Watchlist[]>(() =>
    universeId ? readJSON<Store>(STORE_KEY, {})[universeId] ?? [] : [],
  );
  const [activeId, setActiveIdState] = useState<string | null>(() =>
    universeId ? readJSON<ActiveMap>(ACTIVE_KEY, {})[universeId] ?? null : null,
  );

  // Reload when the selected universe changes, and keep tabs in sync.
  useEffect(() => {
    setLists(universeId ? readJSON<Store>(STORE_KEY, {})[universeId] ?? [] : []);
    setActiveIdState(universeId ? readJSON<ActiveMap>(ACTIVE_KEY, {})[universeId] ?? null : null);
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORE_KEY) setLists(readJSON<Store>(STORE_KEY, {})[universeId] ?? []);
      if (e.key === ACTIVE_KEY)
        setActiveIdState(readJSON<ActiveMap>(ACTIVE_KEY, {})[universeId] ?? null);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [universeId]);

  const persist = useCallback(
    (next: Watchlist[]) => {
      setLists(next);
      const store = readJSON<Store>(STORE_KEY, {});
      if (next.length) store[universeId] = next;
      else delete store[universeId];
      writeJSON(STORE_KEY, store);
    },
    [universeId],
  );

  const setActiveId = useCallback(
    (id: string | null) => {
      setActiveIdState(id);
      const map = readJSON<ActiveMap>(ACTIVE_KEY, {});
      if (id) map[universeId] = id;
      else delete map[universeId];
      writeJSON(ACTIVE_KEY, map);
    },
    [universeId],
  );

  const createWatchlist = useCallback(
    (name?: string): Watchlist => {
      const wl: Watchlist = {
        id: newId(),
        name: name?.trim() || `Watchlist ${lists.length + 1}`,
        tickers: [],
      };
      persist([...lists, wl]);
      setActiveId(wl.id);
      return wl;
    },
    [lists, persist, setActiveId],
  );

  const renameWatchlist = useCallback(
    (id: string, name: string) => {
      persist(lists.map((w) => (w.id === id ? { ...w, name } : w)));
    },
    [lists, persist],
  );

  const deleteWatchlist = useCallback(
    (id: string) => {
      persist(lists.filter((w) => w.id !== id));
      if (activeId === id) setActiveId(null);
    },
    [lists, persist, activeId, setActiveId],
  );

  const toggleTicker = useCallback(
    (id: string, ticker: string) => {
      persist(
        lists.map((w) =>
          w.id === id
            ? {
                ...w,
                tickers: w.tickers.includes(ticker)
                  ? w.tickers.filter((t) => t !== ticker)
                  : [...w.tickers, ticker],
              }
            : w,
        ),
      );
    },
    [lists, persist],
  );

  const setMembers = useCallback(
    (id: string, tickers: string[]) => {
      persist(lists.map((w) => (w.id === id ? { ...w, tickers: [...tickers] } : w)));
    },
    [lists, persist],
  );

  const activeWatchlist = lists.find((w) => w.id === activeId) ?? null;

  return {
    watchlists: lists,
    activeId: activeWatchlist ? activeId : null, // a deleted/stale id reads as "All names"
    activeWatchlist,
    setActiveId,
    createWatchlist,
    renameWatchlist,
    deleteWatchlist,
    toggleTicker,
    setMembers,
  };
}
