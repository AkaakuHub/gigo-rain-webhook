from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StoreRecord:
    store_id: str
    name: str
    prefecture: str
    address: str
    source_url: str
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True)
class Store:
    store_id: str
    name: str
    prefecture: str
    address: str
    latitude: float
    longitude: float
    source_url: str


@dataclass(frozen=True)
class RainResult:
    store: Store
    probability: int
