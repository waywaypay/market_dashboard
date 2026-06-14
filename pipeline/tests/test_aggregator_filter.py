"""Headline hygiene: the press-release/aggregator-index gate that drops topic
landing pages (e.g. GlobeNewswire's "Biotechnology Press Release News") at the
source stage before they reach the brief."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pipeline.contracts import RawItem
from pipeline.providers.util import is_aggregator_page
from pipeline.stages.source import drop_aggregator_pages

NOW = datetime(2026, 6, 10, 13, 0, tzinfo=timezone.utc)


# -- is_aggregator_page: drops press-release / news index pages -------------


@pytest.mark.parametrize(
    "title,text",
    [
        # GlobeNewswire topic landing page surfaced by sector-keyword search
        ("Biotechnology Press Release News | GlobeNewswire",
         "Biotechnology News Browse the latest Biotechnology news."),
        ("Scientific Research Breaking News and Press Releases",
         "Stay updated on research with press releases highlighting industry news."),
        ("Genomics News and Press Releases", "View all press releases."),
    ],
)
def test_drops_aggregator_index_pages(title: str, text: str) -> None:
    assert is_aggregator_page(title, text) is True


@pytest.mark.parametrize(
    "title,text",
    [
        # genuine stories — including real wire press releases about an event —
        # carry none of the index-page markers and must be kept
        ("Veracyte announces expanded Medicare coverage for Decipher",
         "Veracyte said CMS will reimburse the test starting next quarter."),
        ("Natera reports Q2 revenue above guidance, raises outlook",
         "The genetic testing company beat estimates on Signatera volume."),
        ("Guardant Health and AstraZeneca expand liquid-biopsy partnership",
         "The companies will co-develop a companion diagnostic."),
    ],
)
def test_keeps_genuine_headlines(title: str, text: str) -> None:
    assert is_aggregator_page(title, text) is False


def test_empty_is_kept() -> None:
    assert is_aggregator_page("") is False
    assert is_aggregator_page("", "") is False


# -- source-stage integration ----------------------------------------------


def _item(id: str, title: str, raw_text: str | None = None) -> RawItem:
    return RawItem(
        id=id, source="news", feed="news", url=f"https://x.com/{id}",
        title=title, raw_text=raw_text if raw_text is not None else title, ts=NOW,
    )


def test_drop_aggregator_pages_filters_the_brief() -> None:
    items = [
        _item("a", "Biotechnology Press Release News",
              "Biotechnology News Browse the latest Biotechnology news."),
        _item("b", "Veracyte announces expanded Medicare coverage for Decipher"),
        _item("c", "Scientific Research Breaking News and Press Releases",
              "Stay updated on research with press releases."),
        _item("d", "Natera reports Q2 revenue above guidance"),
    ]
    kept = {i.id for i in drop_aggregator_pages(items)}
    assert kept == {"b", "d"}
