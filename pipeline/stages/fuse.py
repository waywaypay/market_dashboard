"""Fuse stage — the signature feature: deterministic price <-> news attribution.

No LLM here. (items, quotes, universe) -> FuseResult:

  * flag unusual moves:  abs(chg) >= sigma_multiple * sigma  OR  rvol >= threshold
  * for each flagged ticker, attribute the most likely driver = the
    highest-materiality same-ticker item in the window (tie: latest ts);
    attach driver_item_id to the quote and mark the item as driver
  * every item whose ticker has a quote gets a price_reaction badge
    (did it move, how much, on what RVOL)
"""

from __future__ import annotations

from pydantic import BaseModel

from pipeline.contracts import Item, PriceReaction, Quote, UniverseConfig


class FuseResult(BaseModel):
    items: list[Item]
    quotes: list[Quote]
    priority_signals: list[Item]


def _flag(quote: Quote, universe: UniverseConfig) -> Quote:
    t = universe.thresholds
    reasons = []
    if quote.sigma > 0 and abs(quote.chg_pct) >= t.sigma_multiple * quote.sigma:
        reasons.append("sigma")
    if quote.rvol is not None and quote.rvol >= t.rvol:
        reasons.append("rvol")
    if not reasons:
        return quote
    return quote.model_copy(update={"flagged": True, "flag_reason": "+".join(reasons)})


def _driver_for(ticker: str, items: list[Item]) -> Item | None:
    candidates = [i for i in items if i.ticker == ticker]
    if not candidates:
        return None
    # highest materiality wins; ties go to the most recent item
    return sorted(candidates, key=lambda i: (-i.materiality, -i.ts.timestamp(), i.id))[0]


def run_fuse(items: list[Item], quotes: list[Quote], universe: UniverseConfig) -> FuseResult:
    quotes = [_flag(q, universe) for q in quotes]
    quote_by_ticker = {q.ticker: q for q in quotes}

    # attribution for flagged tickers
    driver_ids: set[str] = set()
    fused_quotes: list[Quote] = []
    for q in quotes:
        if q.flagged:
            driver = _driver_for(q.ticker, items)
            if driver is not None:
                q = q.model_copy(update={"driver_item_id": driver.id})
                driver_ids.add(driver.id)
        fused_quotes.append(q)

    # price-reaction badge on every item whose ticker trades
    fused_items: list[Item] = []
    for item in items:
        update: dict = {"is_driver": item.id in driver_ids}
        q = quote_by_ticker.get(item.ticker) if item.ticker else None
        if q is not None:
            update["price_reaction"] = PriceReaction(
                ticker=q.ticker, chg_pct=q.chg_pct, rvol=q.rvol, flagged=q.flagged
            )
        fused_items.append(item.model_copy(update=update))

    hot = universe.thresholds.hot_materiality
    priority = [i for i in fused_items if i.materiality >= hot or i.is_driver]
    priority.sort(key=lambda i: (-i.materiality, not i.is_driver, i.ts, i.id))

    return FuseResult(items=fused_items, quotes=fused_quotes, priority_signals=priority)
