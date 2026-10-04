"""SQLite merchant registry used by the verification API."""
import csv
import math
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.environ.get("SHIELDPAY_DB", ROOT / "instance" / "shieldpay.sqlite3"))
MIN_SHOP_RADIUS_M = 5 * 0.3048
MERCHANT_LOCATIONS = {
    "M001": (12.6819, 79.9888),  # Chengalpattu
    "M002": (12.9249, 80.1000),  # Tambaram
    "M003": (12.9516, 80.1462),  # Chromepet
    "M004": (13.0067, 80.2206),  # Guindy
    "M005": (13.0339, 80.2619),  # Mylapore
    "M006": (13.0850, 80.2101),  # Anna Nagar
}
FALLBACK_LOCATION = (12.9716, 80.2206)  # Chennai demo coordinate
DEMO_AREAS = {
    "M001": "Chengalpattu",
    "M002": "Tambaram",
    "M003": "Chromepet",
    "M004": "Guindy",
    "M005": "Mylapore",
    "M006": "Anna Nagar",
}


def connect(database_path=None):
    """Open the configured database and return rows by column name."""
    path = Path(database_path or DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def database_connection(database_path=None):
    """Commit successful work and always close the SQLite connection."""
    connection = connect(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(csv_path=None, database_path=None):
    """Create the schema, add missing CSV records, and apply demo location settings."""
    schema_path = ROOT / "schema.sql"
    source_path = Path(csv_path or ROOT / "merchants.csv")
    with database_connection(database_path) as connection:
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        _migrate_registry_columns(connection)
        connection.execute(
            "UPDATE merchants SET radius_m = ? WHERE radius_m = 100",
            (MIN_SHOP_RADIUS_M,),
        )
        with source_path.open(newline="", encoding="utf-8") as source:
            merchants = list(csv.DictReader(source))

        for index, merchant in enumerate(merchants):
            latitude, longitude = MERCHANT_LOCATIONS.get(
                merchant["merchant_id"], FALLBACK_LOCATION
            )
            connection.execute(
                """INSERT OR IGNORE INTO merchants
                   (merchant_id, vpa, merchant_name, category_code,
                          latitude, longitude, radius_m, shop_address,
                          merchant_registration_number, bank_verified_merchant_id,
                          registered_location_id, registry_verification_status)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'sample_only')""",
                (
                    merchant["merchant_id"].strip(),
                    merchant["vpa"].strip().lower(),
                    merchant["merchant_name"].strip(),
                    merchant.get("category_code", "").strip(),
                    latitude + (index % 3) * 0.00015 if merchant["merchant_id"] not in MERCHANT_LOCATIONS else latitude,
                    longitude + (index % 3) * 0.00015 if merchant["merchant_id"] not in MERCHANT_LOCATIONS else longitude,
                    MIN_SHOP_RADIUS_M,
                    _demo_address(merchant["merchant_id"]),
                    f"DEMO-REG-{merchant['merchant_id']}",
                    f"DEMO-LOC-{merchant['merchant_id']}",
                )
            )

        _mark_existing_records_as_demo(connection)
        _apply_csv_location_overrides(connection, merchants)


def _migrate_registry_columns(connection):
    """Add registry metadata columns to databases created by the earlier app."""
    existing = {
        row["name"] for row in connection.execute("PRAGMA table_info(merchants)")
    }
    additions = {
        "shop_address": "TEXT",
        "merchant_registration_number": "TEXT",
        "bank_verified_merchant_id": "TEXT",
        "registered_location_id": "TEXT",
        "registry_verification_status": "TEXT NOT NULL DEFAULT 'sample_only'",
    }
    for column, definition in additions.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE merchants ADD COLUMN {column} {definition}")


def _demo_address(merchant_id):
    """Return an honest area label, not a fabricated verified street address."""
    area = DEMO_AREAS.get(merchant_id, "Chennai")
    return f"Demo location near {area}; address not independently verified"


def _mark_existing_records_as_demo(connection):
    """Backfill safe demo placeholders without claiming external verification."""
    rows = connection.execute(
        "SELECT merchant_id FROM merchants WHERE shop_address IS NULL"
    ).fetchall()
    for row in rows:
        merchant_id = row["merchant_id"]
        connection.execute(
            """UPDATE merchants
               SET shop_address = ?,
                   merchant_registration_number = COALESCE(merchant_registration_number, ?),
                   registered_location_id = COALESCE(registered_location_id, ?),
                   registry_verification_status = 'sample_only'
               WHERE merchant_id = ?""",
            (
                _demo_address(merchant_id),
                f"DEMO-REG-{merchant_id}",
                f"DEMO-LOC-{merchant_id}",
                merchant_id,
            ),
        )


def _apply_csv_location_overrides(connection, merchants):
    """Apply configured shop addresses and precise coordinates to seeded records."""
    for merchant in merchants:
        merchant_id = merchant.get("merchant_id", "").strip()
        address = (merchant.get("shop_address") or "").strip()
        latitude_text = (merchant.get("latitude") or "").strip()
        longitude_text = (merchant.get("longitude") or "").strip()
        radius_text = (merchant.get("radius_m") or "").strip()

        if not address and not latitude_text and not longitude_text and not radius_text:
            continue
        if address and (not latitude_text or not longitude_text):
            raise ValueError(
                f"Merchant {merchant_id} has a shop address but is missing latitude or longitude."
            )
        if bool(latitude_text) != bool(longitude_text):
            raise ValueError(
                f"Merchant {merchant_id} must have both latitude and longitude."
            )

        values = ["shop_address = ?"]
        parameters = [address] if address else [None]
        if latitude_text and longitude_text:
            try:
                latitude, longitude = float(latitude_text), float(longitude_text)
            except ValueError as error:
                raise ValueError(
                    f"Merchant {merchant_id} has invalid latitude or longitude."
                ) from error
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise ValueError(
                    f"Merchant {merchant_id} coordinates are outside valid ranges."
                )
            values.extend(("latitude = ?", "longitude = ?"))
            parameters.extend((latitude, longitude))
        if radius_text:
            try:
                radius_m = float(radius_text)
            except ValueError as error:
                raise ValueError(
                    f"Merchant {merchant_id} has an invalid geofence radius."
                ) from error
            if not math.isfinite(radius_m) or radius_m <= 0:
                raise ValueError(
                    f"Merchant {merchant_id} geofence radius must be a positive number."
                )
            values.append("radius_m = ?")
            parameters.append(radius_m)
        parameters.append(merchant_id)
        connection.execute(
            f"UPDATE merchants SET {', '.join(values)} WHERE merchant_id = ?",
            parameters,
        )


def list_merchants(database_path=None):
    """Return all merchant records as dictionaries."""
    with database_connection(database_path) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM merchants ORDER BY merchant_id"
        )]


def get_merchant_by_vpa(vpa, database_path=None):
    """Find one merchant using a case-insensitive VPA lookup."""
    with database_connection(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM merchants WHERE lower(vpa) = lower(?)", (vpa or "",)
        ).fetchone()
        return dict(row) if row else None