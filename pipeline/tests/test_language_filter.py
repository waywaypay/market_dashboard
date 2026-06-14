"""Headline hygiene: clean_title normalization + the English-only gate that
drops foreign stories at the source stage."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pipeline.contracts import RawItem
from pipeline.providers.util import clean_title, is_probably_english
from pipeline.stages.source import drop_non_english

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


# -- source-stage integration ----------------------------------------------


def _item(id: str, title: str) -> RawItem:
    return RawItem(
        id=id, source="news", feed="news", url=f"https://x.com/{id}",
        title=title, raw_text=title, ts=NOW,
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
