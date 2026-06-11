/**
 * The research cockpit. Skim-first, dense, single scroll:
 * header band → The Read → market strip → priority signals → by company →
 * sector headlines, with the control rail on the right (top on mobile).
 *
 * Signature interaction: hover state links flagged market tiles to their
 * attributed priority signal (driver_item_id) in both directions.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Header } from "./components/Header";
import { TheRead } from "./components/TheRead";
import { MarketStrip } from "./components/MarketStrip";
import { PrioritySignals } from "./components/PrioritySignals";
import { ByCompany } from "./components/ByCompany";
import { SectorHeadlines } from "./components/SectorHeadlines";
import { RightRail } from "./components/RightRail";
import { EmailModal } from "./components/EmailModal";
import type { DailyBrief, Item, UniverseEntry } from "./lib/contracts";
import { loadBrief, loadUniverses, refreshPipeline } from "./lib/loadBrief";

type LoadState =
  | { phase: "loading" }
  | { phase: "error"; message: string }
  | { phase: "ready"; brief: DailyBrief };

export default function App() {
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [universes, setUniverses] = useState<UniverseEntry[]>([]);
  const [refreshing, setRefreshing] = useState(false);

  // hover linkage (tile <-> signal)
  const [hoverTicker, setHoverTicker] = useState<string | null>(null);
  const [hoverItemId, setHoverItemId] = useState<string | null>(null);

  // rail filters
  const [activeCategories, setActiveCategories] = useState<Set<string>>(new Set());
  const [minMateriality, setMinMateriality] = useState(1);
  const [emailOpen, setEmailOpen] = useState(false);

  const fetchBrief = useCallback(async (universeId?: string) => {
    setRefreshing(true);
    try {
      const brief = await loadBrief(universeId);
      setState({ phase: "ready", brief });
      setActiveCategories(new Set(brief.categories)); // reset filters per universe
      setMinMateriality(1);
    } catch (err) {
      setState({
        phase: "error",
        message:
          err instanceof Error ? err.message : "artifact failed to load or validate",
      });
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadUniverses().then(setUniverses);
    void fetchBrief();
  }, [fetchBrief]);

  // ↻ = re-run the pipeline (where a server exists), then re-read the artifact
  const hardRefresh = useCallback(
    async (universeId: string) => {
      setRefreshing(true);
      await refreshPipeline();
      await fetchBrief(universeId);
    },
    [fetchBrief],
  );

  const brief = state.phase === "ready" ? state.brief : null;

  const visible = useMemo(() => {
    return (item: Item) =>
      item.materiality >= minMateriality &&
      (activeCategories.size === 0 || activeCategories.has(item.category));
  }, [activeCategories, minMateriality]);

  const onTileHover = useCallback((ticker: string | null, driverItemId: string | null) => {
    setHoverTicker(ticker);
    setHoverItemId(driverItemId);
  }, []);
  const onSignalHover = useCallback((itemId: string | null, ticker: string | null) => {
    setHoverItemId(itemId);
    setHoverTicker(ticker);
  }, []);

  if (state.phase === "loading") {
    return (
      <Shell>
        <p className="px-6 py-16 text-sm text-muted">Reading this morning's artifact…</p>
      </Shell>
    );
  }
  if (state.phase === "error" || !brief) {
    return (
      <Shell>
        <div className="mx-auto max-w-[640px] px-6 py-16">
          <h1 className="font-display text-lg font-semibold text-ink">No brief to read.</h1>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            The artifact didn't load ({state.phase === "error" ? state.message : "unknown"}).
            Run <code className="num rounded-sm bg-ink/5 px-1 py-0.5">make run-pipeline</code>{" "}
            to generate <code className="num rounded-sm bg-ink/5 px-1 py-0.5">web/public/brief.json</code>,
            then refresh.
          </p>
        </div>
      </Shell>
    );
  }

  return (
    <div className="min-h-screen bg-surface pb-10">
      <a
        href="#signals"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-sm focus:bg-ink focus:px-3 focus:py-2 focus:text-sm focus:text-white"
      >
        Skip to priority signals
      </a>
      <Header
        brief={brief}
        universes={universes}
        onSelectUniverse={(id) => void fetchBrief(id)}
        onRefresh={() => void hardRefresh(brief.universe_id)}
        refreshing={refreshing}
      />
      <TheRead brief={brief} />

      <div className="mx-auto flex max-w-[1400px] flex-col gap-0 lg:flex-row lg:gap-6 lg:px-6">
        <main className="min-w-0 flex-1">
          <MarketStrip
            brief={brief}
            hoverTicker={hoverTicker}
            hoverItemId={hoverItemId}
            onHover={onTileHover}
          />
          <div id="signals">
            <PrioritySignals
              brief={brief}
              visible={visible}
              hoverTicker={hoverTicker}
              hoverItemId={hoverItemId}
              onHover={onSignalHover}
            />
          </div>
          <ByCompany brief={brief} visible={visible} />
          <SectorHeadlines brief={brief} visible={visible} />
        </main>

        <div className="order-first px-4 pt-6 sm:px-6 lg:order-none lg:w-[300px] lg:shrink-0 lg:px-0 lg:pt-6">
          <RightRail
            brief={brief}
            activeCategories={activeCategories}
            onToggleCategory={(cat) =>
              setActiveCategories((prev) => {
                const next = new Set(prev);
                if (next.has(cat)) next.delete(cat);
                else next.add(cat);
                return next;
              })
            }
            minMateriality={minMateriality}
            onMinMateriality={setMinMateriality}
            onGenerate={() => setEmailOpen(true)}
          />
        </div>
      </div>

      {emailOpen && <EmailModal brief={brief} onClose={() => setEmailOpen(false)} />}
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-surface">
      <div className="bg-ink px-6 py-4">
        <span className="font-display text-[15px] font-semibold tracking-wide text-white">
          Pre-Market Read
        </span>
      </div>
      {children}
    </div>
  );
}
