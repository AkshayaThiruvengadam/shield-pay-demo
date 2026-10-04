CREATE TABLE IF NOT EXISTS merchants (
    merchant_id TEXT PRIMARY KEY,
    vpa TEXT NOT NULL UNIQUE COLLATE NOCASE,
    merchant_name TEXT NOT NULL,
    category_code TEXT,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    radius_m REAL NOT NULL DEFAULT 1.524,
    shop_address TEXT,
    merchant_registration_number TEXT,
    bank_verified_merchant_id TEXT,
    registered_location_id TEXT,
    registry_verification_status TEXT NOT NULL DEFAULT 'sample_only'
);

CREATE INDEX IF NOT EXISTS idx_merchants_vpa ON merchants(vpa);