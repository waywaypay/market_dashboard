# Pre-Market Read

A morning competitive-intelligence + market-analytics product for IR and equity
professionals who cover a defined peer set. One job: **in ~60 seconds, tell you
what moved overnight and what you need to know before the open** — then let you
drill in and ship a morning email ("First Read").

Two fused layers:

1. **News brief** — RSS + SEC EDGAR + news search, deduped, then classified and
   summarized by Claude in a single batched call (category, materiality 1–5,
   subject-relevance, house-style summary, one-line TL;DR).
2. **Market analytics** — pre-market price/volume per ticker, unusual-move
   detection (`abs(%chg) ≥ 2σ` or `RVOL ≥ 2`), and the signature feature:
   deterministic **price↔news attribution** — every flagged move is linked to
   its most likely news driver, and every material item carries a
   price-reaction badge.

Nothing is hardcoded to a company or sector. The entire product is driven by a
swappable **universe config** (`universes/*.yaml`); two ship in this repo
(diagnostics & genomics, consumer fintech) so generalization is provable by
flipping the selector.

## Quickstart (zero API keys)

```sh
make setup   # python venv + pip install + npm install (one time)
make dev     # run the pipeline on fixtures, then serve the dashboard
```

Open http://localhost:5173. Everything you see — both universes, three seeded
unusual moves each, a stale EDGAR feed, the email preview — runs from synthetic
fixtures with no provider accounts and no keys.

```sh
make test    # unit + smoke tests, web typecheck
make eval    # gate evals (classify/summarize handoff gate is the priority gate)
```

## Architecture

Two services, one repo. `pipeline/` is Python; `web/` is React + Vite + TS +
Tailwind, a **read-only consumer** of the pipeline's artifact plus one action
(ship the email). Deterministic orchestration over LLM routing: the DAG is
fixed in `pipeline/orchestrator.py`, and the only LLM call lives inside the
process stage.

```
universes/*.yaml ──► orchestrator (deterministic DAG, no LLM routing)
                       │
   source stage        │  parallel fetch behind vendor-neutral interfaces:
                       │  RSS · EDGAR · news search · quotes
                       │  → look-ahead filter → dedupe (URL + fuzzy title) → RVOL
   process stage       │  ONE batched Claude call → strict JSON Classification
                       │  (validate → retry once → rule-based fallback; never crashes)
   fuse stage          │  flag unusual moves; attribute driver_item_id;
                       │  attach price_reaction badges  (pure Python, no LLM)
   output stage        │  DailyBrief artifact → web/public/brief.json
                       ▼  + First Read email rendered from the SAME artifact
              web/public/briefs/<id>.json ──► dashboard (and /api/ship → email)
```

**Contracts are the only cross-stage interface.** Pydantic models in
`pipeline/contracts/` define `RawItem → Classification → Item → DailyBrief`;
the web app mirrors them in zod (`web/src/lib/contracts.ts`) and validates the
artifact on load. Dashboard and email render from the same `DailyBrief`, so
they can never disagree.

### Providers (vendor-neutral by construction)

Every external dependency sits behind a typed interface in
`pipeline/providers/base.py` with a `Fixture*` reference implementation:

| Interface            | Fixture (default)                  | Real implementation                                       |
| -------------------- | ---------------------------------- | --------------------------------------------------------- |
| `RSSProvider`        | synthetic feed items               | **`HttpRSSProvider` (working)** — feed URLs in the YAML   |
| `EdgarProvider`      | synthetic 8-Ks                     | **`SecEdgarProvider` (working)** — free SEC submissions API|
| `NewsProvider`       | synthetic search results           | **`ExaNewsProvider` (working)** — Exa semantic news search|
| `QuoteProvider`      | seeded pre-market snapshot         | `MarketDataQuoteProvider` (stub + TODO)                   |
| `ClassifierProvider` | canned labels + rule-based backup  | `AnthropicClassifierProvider` (working)                   |
| `EmailProvider`      | writes `.html` to `out/emails/`    | `SmtpEmailProvider` (stub + TODO)                         |

Selection is env-driven (never LLM-driven). `BRIEF_PROVIDERS=fixture|real`
sets the global default; `BRIEF_RSS` / `BRIEF_EDGAR` / `BRIEF_NEWS` /
`BRIEF_QUOTES` / `BRIEF_EMAIL` override per provider, so you can mix real news
with fixture quotes while the quote vendor is undecided. Provider failures
surface as `SourceHealth` entries in the artifact (with the reason, rendered
in the rail), not crashes.

### Going real

```sh
export SEC_EDGAR_USER_AGENT="yourapp/1.0 you@example.com"  # SEC fair-access policy
export EXA_API_KEY="..."                                   # https://exa.ai
export ANTHROPIC_API_KEY="..."                             # real classification

BRIEF_PROVIDERS=real BRIEF_QUOTES=fixture BRIEF_EMAIL=fixture make run-pipeline
```

* **EDGAR** — no key needed. Resolves ticker→CIK, pulls recent 8-K/8-K/A
  filings inside `EDGAR_LOOKBACK_HOURS` (default 36), prefers the EX-99.*
  press exhibit body, titles filings by item code ("results of operations",
  "material definitive agreement", …), and throttles well under SEC's 10 req/s.
* **RSS** — pulls every `rss_feeds` entry with a `url:`; label-only entries
  (paywalled publications) stay fixture-only. Per-feed failures are tolerated;
  the pull only fails if *every* feed does.
* **News (Exa)** — two bounded semantic searches per run (company names incl.
  private watch; sector keywords) against `POST /search` with
  `category: news` and a published-date window (`EXA_LOOKBACK_HOURS`, default
  36; `EXA_NUM_RESULTS` per query, default 10). Undated results are dropped —
  they can't pass the no-look-ahead gate.
* **Classifier** — `BRIEF_CLASSIFIER=auto` (default) uses Claude when
  `ANTHROPIC_API_KEY` is set, else fixtures — same eval gates either way.

The cross-source dedupe, ticker inference, look-ahead guard, fuse attribution,
and all eval gates apply identically to real data. Wire-format correctness is
pinned by mocked-transport tests in `pipeline/tests/test_real_providers.py`.

### Universe config — the generalization mechanism

A universe YAML drives everything downstream: feeds and tickers to pull, the
classifier's taxonomy + house voice, unusual-move thresholds, dashboard
presentation (subject pinning, category colors by taxonomy order, display tz),
and email recipients. Add a sector by adding a YAML file — no code changes.
See `universes/diagnostics.yaml` for the annotated reference.

### Evals (`make eval`) — these gate the pipeline

* **Classify/summarize handoff gate** (highest priority): fixture `RawItem`s
  with gold `category` / materiality-band / `is_subject_relevant` labels per
  universe. Asserts the classifier's output validates against the contract,
  stays inside the configured taxonomy, and matches gold within tolerance —
  tight bars for the fixture/Claude engine, a looser floor for the rule-based
  fallback. With `ANTHROPIC_API_KEY` set, the same gate exercises real Claude.
* **Fuse attribution**: deterministic checks that each seeded unusual move is
  flagged for the right reason (`sigma`/`rvol`) and attributed to the right
  driver — including the case where a lower-materiality same-ticker story must
  lose.
* **No-look-ahead**: a future-timestamped fixture item must never appear, and
  nothing in the artifact may be stamped after `generated_at`.

## The dashboard

Skim-first, dense, top-to-bottom: header band (live "opens in mm:ss"
countdown, universe selector, refresh) → **The Read** (TL;DR + counts) →
sortable market strip (subject pinned) → priority signals → collapsible
by-company cards → sector headlines, with source health, category filters, a
materiality slider, and **Generate today's First Read →** in the right rail.

Hovering a flagged tile highlights its attributed signal via `driver_item_id`,
and hovering a signal highlights its ticker's tile — the price↔news link is
the product's point of view, rendered as an interaction.

"Ship it" POSTs `/api/ship`; the Vite dev server shells out to
`python -m pipeline.ship`, which re-renders the email from the artifact and
sends via the configured `EmailProvider` (fixture writes to `out/emails/`).

## Repo layout

```
pipeline/
  contracts/      Pydantic models — the ONLY cross-stage interface
  providers/      interfaces + FixtureProvider + Anthropic classifier + real stubs
  stages/         source → process → fuse → output (pure, typed)
  orchestrator.py deterministic DAG + cron-style entrypoint
  ship.py         re-render + send the email for an existing artifact
  evals/          gate evals + gold labels
  fixtures/       synthetic inputs for both universes
  tests/          unit + conformance + smoke
web/
  src/lib/        zod contracts, artifact loader, formatters
  src/components/ the cockpit
  public/         brief.json, briefs/<id>.json, universes.json (pipeline output)
universes/        diagnostics.yaml, fintech.yaml
```

## Out of scope (deliberately)

No auth, no DB (the artifact is a JSON file), no hosted scheduler, no
multi-user. One batched LLM call per run; everything else is deterministic
Python.
