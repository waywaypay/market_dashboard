"""Headline hygiene: clean_title normalization, the English-only gate, and the
press-release/aggregator-index gate — both drop junk at the source stage."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pipeline.contracts import RawItem
from pipeline.providers.util import clean_title, is_aggregator_page, is_probably_english
from pipeline.stages.source import drop_aggregator_pages, drop_non_english

NOW = datetime(2026, 6, 10, 13, 0, tzinfo=timezone.utc)


# -- clean_title ------------------------------------------------------------


def test_clean_title_unescapes_entities() -> None:
    assert clean_title("Affirm &amp; Walmart strike &#8217;deal&#8217;") == (
        "Affirm & Walmart strike ’deal’"
    )


def test_clean_title_strips_markup_and_whitespace() -> None:
    assert clean_title("  <b>Block</b>   beats\non revenue ") == "Block beats on revenue"


def test_clean_title_trims_matching_publisher_suffix() -> None:
    assert clean_title("Stripe valuation jumps - Finextra", "Finextra") == (
        "Stripe valuation jumps"
    )
    # case-insensitive, and other common separators
    assert clean_title("PayPal launches wallet | Payments Dive", "payments dive") == (
        "PayPal launches wallet"
    )


def test_clean_title_keeps_dash_that_is_not_the_publisher() -> None:
    # the trailing token isn't the feed label, so the dash is real title content
    assert clean_title("Affirm: The Road Ahead - Part Two", "Finextra") == (
        "Affirm: The Road Ahead - Part Two"
    )


# -- is_probably_english: keeps English ------------------------------------


@pytest.mark.parametrize(
    "headline",
    [
        "Affirm beats on revenue, raises full-year guidance",
        "Block partners with Walmart to launch new checkout",
        "Upstart preannounces Q2 revenue above guidance",
        "SoFi", "AFRM +12% premarket",  # too short to judge -> kept
        "Affirm Walmart OnePay Deal Expands",  # all proper nouns -> kept
    ],
)
def test_keeps_english(headline: str) -> None:
    assert is_probably_english(headline) is True


def test_empty_is_kept() -> None:
    assert is_probably_english("") is True
    assert is_probably_english("", "") is True


# -- is_probably_english: drops foreign ------------------------------------


@pytest.mark.parametrize(
    "headline",
    [
        "Affirm lanza pago a plazos con Walmart en México",          # Spanish
        "Block annonce un nouveau partenariat avec PayPal",          # French
        "Affirm startet eine Partnerschaft mit der Walmart-Gruppe",  # German
        "Nubank lança novo cartão para o mercado brasileiro",        # Portuguese
        "アファームがウォルマートと提携を発表",                      # Japanese
        "蚂蚁集团宣布与沃尔玛达成新的支付合作协议",                    # Chinese
        "Афинио объявляет о новом партнёрстве с Walmart",            # Russian
        "أفيرم تعلن عن شراكة جديدة مع وول مارت",                     # Arabic
    ],
)
def test_drops_foreign(headline: str) -> None:
    assert is_probably_english(headline) is False


def test_mixed_mostly_english_with_foreign_brand_is_kept() -> None:
    # a stray non-Latin brand token shouldn't sink an otherwise-English headline
    assert is_probably_english("Sony 索尼 reports record quarterly earnings today") is True


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


def test_aggregator_empty_is_kept() -> None:
    assert is_aggregator_page("") is False
    assert is_aggregator_page("", "") is False


# -- source-stage integration ----------------------------------------------


def _item(id: str, title: str, raw_text: str | None = None) -> RawItem:
    return RawItem(
        id=id, source="news", feed="news", url=f"https://x.com/{id}",
        title=title, raw_text=raw_text if raw_text is not None else title, ts=NOW,
    )


def test_drop_non_english_filters_the_brief() -> None:
    items = [
        _item("a", "Affirm signs exclusive BNPL deal with Walmart"),
        _item("b", "蚂蚁集团宣布与沃尔玛达成新的支付合作协议"),
        _item("c", "Block annonce un nouveau partenariat avec PayPal"),
        _item("d", "Upstart reinstates full-year revenue guidance"),
    ]
    kept = {i.id for i in drop_non_english(items)}
    assert kept == {"a", "d"}


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
