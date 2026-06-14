"""HTML email renderer — "First Read".

Single inline-CSS, table-based, mobile-safe email generated from the
DailyBrief artifact (the same object the dashboard renders, so the two never
disagree). Sections, in order: subject-company news -> comp-by-comp ->
sector headlines. One-click source links throughout.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from pipeline.contracts import DailyBrief, Item

INK = "#12161C"
SURFACE = "#F7F8FA"
CARD = "#FFFFFF"
HAIRLINE = "#E4E7EC"
ACCENT = "#0E7C7B"
UP = "#1A7F4B"
DOWN = "#B42318"
CATEGORY_PALETTE = ["#7C3AED", "#0891B2", "#D97706", "#2563EB"]

_MONO = "'Courier New',Courier,monospace"
_BODY = "Helvetica,Arial,sans-serif"


def _fmt_time(ts: datetime, tz: str) -> str:
    local = ts.astimezone(ZoneInfo(tz))
    return local.strftime("%-I:%M%p").lower() + local.strftime(" %Z").replace("STD", "")


def _cat_color(category: str, categories: list[str]) -> str:
    try:
        return CATEGORY_PALETTE[categories.index(category) % len(CATEGORY_PALETTE)]
    except ValueError:
        return ACCENT


def _badge(item: Item) -> str:
    pr = item.price_reaction
    if pr is None:
        return ""
    color = UP if pr.chg_pct >= 0 else DOWN
    rvol = f" · {pr.rvol:.1f}× vol" if pr.rvol is not None else ""
    flag = " ⚑" if pr.flagged else ""
    return (
        f'<span style="font-family:{_MONO};font-size:12px;color:{color};'
        f'white-space:nowrap;">{pr.chg_pct:+.1f}%{rvol}{flag}</span>'
    )


def _item_row(item: Item, brief: DailyBrief) -> str:
    cat = _cat_color(item.category, brief.categories)
    who = escape(item.company or "Sector")
    tick = f" ({escape(item.ticker)})" if item.ticker else ""
    return f"""
    <tr>
      <td style="padding:12px 0;border-bottom:1px solid {HAIRLINE};">
        <div style="font-family:{_BODY};font-size:12px;color:#667085;margin-bottom:4px;">
          <span style="color:{cat};font-weight:bold;">{escape(item.category).upper()}</span>
          &nbsp;·&nbsp;<span style="font-family:{_MONO};">{_fmt_time(item.ts, brief.display_tz)}</span>
          &nbsp;·&nbsp;{escape(item.source)}
          {"&nbsp;·&nbsp;" + _badge(item) if item.price_reaction else ""}
        </div>
        <div style="font-family:{_BODY};font-size:14px;line-height:21px;color:{INK};">
          <strong>{who}{tick}:</strong> {escape(item.summary)}
          <a href="{escape(item.url)}" style="color:{ACCENT};text-decoration:none;">&#8599;&#xFE0E; source</a>
        </div>
      </td>
    </tr>"""


def _section(title: str, rows: str) -> str:
    if not rows:
        return ""
    return f"""
    <tr><td style="padding:24px 24px 0 24px;">
      <div style="font-family:{_BODY};font-size:11px;letter-spacing:1.5px;color:#667085;
                  font-weight:bold;border-bottom:2px solid {INK};padding-bottom:6px;">{escape(title.upper())}</div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>
    </td></tr>"""


def render_email(brief: DailyBrief) -> str:
    date_str = brief.generated_at.astimezone(ZoneInfo(brief.display_tz)).strftime("%A, %B %-d %Y")

    subject_items = "".join(
        _item_row(i, brief)
        for i in brief.by_company.get(brief.subject_name, [])
    )
    comp_rows = "".join(
        _item_row(i, brief)
        for company, rows in brief.by_company.items()
        if company != brief.subject_name
        for i in rows
    )
    sector_rows = "".join(_item_row(i, brief) for i in brief.sector_headlines)

    # Synthetic data must announce itself in the email exactly like it does
    # on the dashboard — a forwarded First Read has no Sources panel.
    if brief.data_mode == "real":
        provenance_banner = ""
    else:
        label = (
            "SYNTHETIC DEMO DATA" if brief.data_mode == "fixture" else "PARTIALLY SYNTHETIC DATA"
        )
        provenance_banner = (
            f'<tr><td style="background:#FEF3F2;border-bottom:2px solid {DOWN};padding:10px 24px;">'
            f'<span style="font-family:{_BODY};font-size:12px;font-weight:bold;color:{DOWN};">'
            f"&#9888;&#xFE0E; {label} — fixture providers, not live market data.</span></td></tr>"
        )

    # The narrative First Read is the lede when present; older/keyless artifacts
    # without one fall back to the one-line tldr (which is otherwise untouched).
    lede = brief.first_read.strip() or brief.tldr
    first_read_note = (
        f" · first read: {escape(brief.first_read_engine)}"
        if brief.first_read.strip()
        else ""
    )

    movers = [q for q in brief.market if q.flagged]
    movers_line = " &nbsp;·&nbsp; ".join(
        f'<span style="font-family:{_MONO};color:{UP if q.chg_pct >= 0 else DOWN};">'
        f"{escape(q.ticker)} {q.chg_pct:+.1f}%</span>"
        for q in movers
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>First Read — {escape(brief.universe_label)}</title></head>
<body style="margin:0;padding:0;background:{SURFACE};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{SURFACE};">
<tr><td align="center" style="padding:16px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="max-width:600px;width:100%;background:{CARD};border:1px solid {HAIRLINE};">
  <tr><td style="background:{INK};padding:20px 24px;">
    <div style="font-family:{_BODY};font-size:11px;letter-spacing:2px;color:{ACCENT};font-weight:bold;">
      &#9638; FIRST READ — {escape(brief.universe_label.upper())}</div>
    <div style="font-family:{_BODY};font-size:13px;color:#98A2B3;margin-top:4px;">{date_str}
      &nbsp;·&nbsp; <span style="font-family:{_MONO};">{brief.counts.total_items} items · {brief.counts.hot_items} hot</span></div>
    <div style="font-family:Georgia,serif;font-size:17px;line-height:25px;color:#FFFFFF;margin-top:12px;">
      {escape(lede)}</div>
    {f'<div style="margin-top:10px;font-size:13px;">{movers_line}</div>' if movers_line else ""}
  </td></tr>
  {provenance_banner}
  {_section(f"{brief.subject_name} ({brief.subject_ticker})", subject_items)
    or _section(brief.subject_name, f'<tr><td style="padding:12px 0;font-family:{_BODY};font-size:13px;color:#667085;">Nothing material on {escape(brief.subject_name)} overnight.</td></tr>')}
  {_section("Comps", comp_rows)}
  {_section("Sector", sector_rows)}
  <tr><td style="padding:20px 24px;background:{SURFACE};border-top:1px solid {HAIRLINE};">
    <div style="font-family:{_BODY};font-size:11px;color:#98A2B3;line-height:17px;">
      Generated {_fmt_time(brief.generated_at, brief.display_tz)} · market opens
      {_fmt_time(brief.market_open_at, brief.display_tz)} · classifier: {escape(brief.classifier_engine)}{first_read_note} · data: {escape(brief.data_mode)}.<br>
      Rendered from the same artifact as the dashboard — they never disagree.</div>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def email_subject(brief: DailyBrief) -> str:
    date_str = brief.generated_at.astimezone(ZoneInfo(brief.display_tz)).strftime("%Y-%m-%d")
    return f"First Read — {brief.universe_label} — {date_str}"
