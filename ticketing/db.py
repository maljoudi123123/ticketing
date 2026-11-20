import os

import pymysql
from pymysql.cursors import DictCursor


def connect() -> pymysql.Connection:
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3307")),
        user=os.getenv("MYSQL_USER", "tickets"),
        password=os.getenv("MYSQL_PASSWORD", "local-tickets"),
        database=os.getenv("MYSQL_DATABASE", "tickets"),
        charset="utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=5,
    )
