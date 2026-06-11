"""Universe config: the generalization mechanism.

A universe is a YAML file (see /universes). Everything downstream — sources to
pull, tickers to quote, the classifier's taxonomy and voice, what the
dashboard renders, who gets the email — is driven by this model. Nothing in
the pipeline or web app is hardcoded to a company or sector.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, field_validator

import yaml


class CompanyRef(BaseModel):
    ticker: str
    name: str


class RSSFeed(BaseModel):
    """A configured feed. `label` is the display/source name (and what the
    fixture provider matches on); `url` is required only for real pulls —
    label-only entries are skipped by the real RSS provider (e.g. paywalled
    publications with no public feed)."""

    label: str
    url: str | None = None


class Thresholds(BaseModel):
    sigma_multiple: float = 2.0
    rvol: float = 2.0
    materiality_floor: int = Field(default=2, ge=1, le=5)
    hot_materiality: int = Field(default=4, ge=1, le=5)
    stale_after_min: int = 75


class Delivery(BaseModel):
    time: str = "07:00"
    tz: str = "America/Los_Angeles"
    recipients: list[str] = []


class UniverseConfig(BaseModel):
    id: str
    label: str
    subject: CompanyRef
    peers: list[CompanyRef]
    private_watch: list[str] = []
    sector_keywords: list[str] = []
    rss_feeds: list[RSSFeed] = []
    categories: list[str] = Field(min_length=1)
    house_style: str
    thresholds: Thresholds = Thresholds()
    delivery: Delivery = Delivery()

    @field_validator("rss_feeds", mode="before")
    @classmethod
    def _coerce_feeds(cls, v: object) -> object:
        # accept plain strings ("GenomeWeb") as label-only feeds
        if isinstance(v, list):
            return [{"label": f} if isinstance(f, str) else f for f in v]
        return v

    @field_validator("categories")
    @classmethod
    def _unique_categories(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("categories must be unique")
        return v

    # -- convenience lookups used across stages --

    @property
    def tickers(self) -> list[str]:
        return [self.subject.ticker] + [p.ticker for p in self.peers]

    @property
    def companies(self) -> dict[str, str]:
        """ticker -> display name (subject + peers)."""
        out = {self.subject.ticker: self.subject.name}
        out.update({p.ticker: p.name for p in self.peers})
        return out


def load_universe(path: str | Path) -> UniverseConfig:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return UniverseConfig.model_validate(data)


def discover_universes(directory: str | Path = "universes") -> list[Path]:
    """All universe YAMLs, sorted for deterministic run order."""
    return sorted(Path(directory).glob("*.yaml"))
