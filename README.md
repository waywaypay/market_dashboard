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
| `QuoteProvider`      | seeded pre-market snapshot         | **Yahoo → Stooq → Alpha Vantage chain (working)** — keyless, self-healing (keyed last resort) |
| `ClassifierProvider` | canned labels + rule-based backup  | `AnthropicClassifierProvider` (working)                   |
| `EmailProvider`      | writes `.html` to `out/emails/`    | `SmtpEmailProvider` (stub + TODO)                         |

Selection is env-driven (never LLM-driven). `BRIEF_PROVIDERS=fixture|real`
sets the global default; `BRIEF_RSS` / `BRIEF_EDGAR` / `BRIEF_NEWS` /
`BRIEF_QUOTES` / `BRIEF_EMAIL` override per provider, so you can mix sources
freely (e.g. real quotes with fixture news while keys are pending). Provider
failures surface as `SourceHealth` entries in the artifact (with the reason,
rendered in the rail), not crashes.

### Going real

```sh
export SEC_EDGAR_USER_AGENT="yourapp/1.0 you@example.com"  # SEC fair-access policy
export EXA_API_KEY="..."                                   # https://exa.ai
export ANTHROPIC_API_KEY="..."                             # real classification
export ALPHAVANTAGE_API_KEY="..."                          # optional: keyed quote fallback

BRIEF_PROVIDERS=real BRIEF_EMAIL=fixture make run-pipeline
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
* **Quotes (Yahoo → Stooq → Alpha Vantage chain)** — no keys needed for the
  first two tiers. Yahoo is primary: ONE
  batched v7 quote call per refresh prices the whole universe (pre/post-
  market price, previous close, day volume, 3-month average volume) behind
  a cookie+crumb handshake done once per process; `sigma` (stdev of the
  last `QUOTES_TRAILING_DAYS` daily % moves, default 20) comes from
  per-ticker daily history, cached per UTC day and best-effort. A
  per-ticker chart fallback covers handshake failures. When Yahoo
  rate-limits the host's shared egress IP entirely (HTTP 429 — common on
  free cloud tiers), the chain falls back to **Stooq**: real exchange data
  over keyless CSV (with `stooq.com`↔`stooq.pl` mirror failover). Stooq's
  daily-history endpoint is the workhorse and stays reachable even from IPs
  that 404 its light-quote tape, so the live tape is treated as a bonus:
  when it answers we show the delayed intraday print, otherwise every ticker
  still prices off its last completed daily close — the market is never left
  blank. With the session shut (weekend/holiday/overnight) that close is
  shown with its own move ("as of close", the convention every finance UI
  uses); during a trading day still awaiting the first print it stays flat,
  never passing off a prior session's move as today's. The next refresh
  retries Yahoo first, so pre-market quality restores itself. Requests are
  paced with capped, `Retry-After`-honoring backoff and a hard time budget
  (`QUOTES_DEADLINE_S`, default 120s). RVOL and unusual-move flags stay
  derived in the fuse stage. When both keyless tiers are blocked at the host
  IP (some cloud egress IPs are banned by Yahoo *and* Stooq), set
  `ALPHAVANTAGE_API_KEY` to add a keyed last-resort tier (`GLOBAL_QUOTE`,
  last price + close move). Its free tier is strict (25 req/day, 5/min), so
  results are cached per ticker for `ALPHAVANTAGE_TTL_S` (default 12h) and
  quota notices abort the batch — fine for "as of close", thin for live
  intraday.
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

## Deploying (Render)

`render.yaml` is a ready Blueprint: in Render choose **New → Blueprint**,
point it at this repo, and it provisions one web service with the right
build/start commands and env vars. **Creating a Web Service manually works
too**, but `render.yaml` is ignored on that path — Render falls back to its
own defaults (`poetry install`, current Python), so you must set the two
commands yourself in the service settings:

```
Build Command:  pip install -r requirements.txt && cd web && npm ci && npm run build
Start Command:  python -m pipeline.serve
```

The repo is robust to Render's auto-detection either way: `.python-version`
pins Python 3.11, `requirements.txt` covers the pip default path,
`[tool.poetry].packages` makes the bare `poetry install` default succeed —
and **`web/dist` ships prebuilt in the repo**, so the dashboard loads
immediately on any deploy path with no build step and no cold-start compile.
Two layers of freshness sit behind that: a deploy build command (when set)
rebuilds `dist` from source, and if `dist` is ever missing the server
self-heals by running `npm ci && npm run build` itself behind a
self-refreshing status page (`BRIEF_AUTO_BUILD=0` disables). If you change
`web/src`, run `make build` and commit the refreshed `dist`.

`pipeline/serve.py` is the single production process: it serves the built
dashboard, serves artifacts with `no-store` freshness, exposes
`POST /api/ship` and `POST /api/refresh` (the ↻ button), health-checks at
`/healthz`, and re-runs the pipeline at boot (in the background — the port
binds immediately so health checks never wait on a pipeline run) plus every
`BRIEF_REFRESH_MINUTES` (default 30, `0` disables). Scheduled refreshes never
send email — only the explicit ship action does. No disk or database needed:
the artifact is recomputed, not persisted, so free-tier spin-down just means
a fresh brief on wake.

Deploys pull real data out of the box — RSS, SEC EDGAR and Yahoo quotes need
no keys. This holds on **every** deploy path: `render.yaml` sets
`BRIEF_PROVIDERS=real` for Blueprint deploys, and the server itself defaults
to real providers when no `BRIEF_*` env is set, which covers manually-created
services (where `render.yaml` is ignored). Set `BRIEF_PROVIDERS=fixture`
explicitly for a synthetic demo deploy. Two sources stay dark until you set
secrets in the Render dashboard:
`EXA_API_KEY` (news search shows "failed" in the rail until then) and
`ANTHROPIC_API_KEY` (classification falls back to rule-based tagging until
then); also set `SEC_EDGAR_USER_AGENT` to your contact per SEC fair-access
policy. If the deploy's egress IP is banned by *both* keyless quote vendors
(Yahoo and Stooq), set `ALPHAVANTAGE_API_KEY` to add the keyed quote tier so
the market strip still fills. Note the service is public by default and the
ship/refresh endpoints
are unauthenticated (auth is out of scope by design) — keep the URL private
or put Render's access controls in front of it.

Local dress rehearsal of exactly what Render runs: `make serve` →
http://localhost:8000.

## Repo layout

```
pipeline/
  contracts/      Pydantic models — the ONLY cross-stage interface
  providers/      interfaces + fixtures + real providers (RSS/EDGAR/Exa/Yahoo/Claude)
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
