from __future__ import annotations

import csv
from pathlib import Path

from scripts.gigo_constants import CSV_FIELDS, PREF_ORDER
from scripts.gigo_models import Store, StoreRecord


def existing_coordinates(csv_path: Path) -> dict[str, tuple[str, float, float]]:
    if not csv_path.exists():
        return {}
    result: dict[str, tuple[str, float, float]] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            store_id = (row.get("store_id") or "").strip()
            address = (row.get("address") or "").strip()
            lat_raw = (row.get("latitude") or "").strip()
            lon_raw = (row.get("longitude") or "").strip()
            if not store_id or not lat_raw or not lon_raw:
                continue
            try:
                result[store_id] = (address, float(lat_raw), float(lon_raw))
            except ValueError:
                continue
    return result


def write_store_csv(records: list[StoreRecord], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    records = sorted(records, key=lambda r: (PREF_ORDER.get(r.prefecture, 999), r.name, r.store_id))
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "store_id": record.store_id,
                    "name": record.name,
                    "prefecture": record.prefecture,
                    "address": record.address,
                    "latitude": "" if record.latitude is None else f"{record.latitude:.6f}",
                    "longitude": "" if record.longitude is None else f"{record.longitude:.6f}",
                    "source_url": record.source_url,
                }
            )


def load_stores(csv_path: str | Path) -> list[Store]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"Store CSV not found: {path}")
    stores: list[Store] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        required = set(CSV_FIELDS)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Store CSV is missing columns: {sorted(missing)}")
        for row_number, row in enumerate(reader, start=2):
            name = (row.get("name") or "").strip()
            pref = (row.get("prefecture") or "").strip()
            lat_raw = (row.get("latitude") or "").strip()
            lon_raw = (row.get("longitude") or "").strip()
            if not name and not pref and not lat_raw and not lon_raw:
                continue
            if not name or not pref or not lat_raw or not lon_raw:
                raise ValueError(f"Store CSV row {row_number} has blank required values: {row}")
            try:
                lat = float(lat_raw)
                lon = float(lon_raw)
            except ValueError as exc:
                raise ValueError(f"Store CSV row {row_number} has invalid coordinates: {row}") from exc
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError(f"Store CSV row {row_number} coordinates are out of range: {row}")
            stores.append(
                Store(
                    store_id=(row.get("store_id") or "").strip(),
                    name=name,
                    prefecture=pref,
                    address=(row.get("address") or "").strip(),
                    latitude=lat,
                    longitude=lon,
                    source_url=(row.get("source_url") or "").strip(),
                )
            )
    if not stores:
        raise ValueError("Store CSV has no stores. Run the manual update workflow first.")
    return stores
