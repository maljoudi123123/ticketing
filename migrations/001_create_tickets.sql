CREATE TABLE tickets (
    id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    subject TEXT NOT NULL,
    body MEDIUMTEXT NOT NULL,
    status ENUM('pending', 'classified', 'failed') NOT NULL DEFAULT 'pending',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
