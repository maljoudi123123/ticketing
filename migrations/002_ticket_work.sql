ALTER TABLE tickets
    ADD COLUMN attempt_count TINYINT UNSIGNED NOT NULL DEFAULT 0,
    ADD COLUMN lease_token CHAR(32) CHARACTER SET ascii COLLATE ascii_bin,
    ADD COLUMN available_at DATETIME(6) NOT NULL DEFAULT (UTC_TIMESTAMP(6)),
    ADD COLUMN category ENUM('billing', 'technical', 'account', 'other'),
    ADD COLUMN priority ENUM('low', 'medium', 'high'),
    ADD COLUMN summary VARCHAR(500),
    ADD COLUMN failure_reason VARCHAR(64),
    ADD INDEX claimable_tickets (status, available_at, id);
