import json
import logging
import os
import time

import psycopg
import redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dakiya-worker")

conn = psycopg.connect(
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", "5432")),
    dbname=os.getenv("DB_NAME", "dakiya"),
    user=os.getenv("DB_USER", "dakiya"),
    password=os.getenv("DB_PASSWORD"),
)

r = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    socket_connect_timeout=5,
    socket_timeout=None,
)

log.info("worker started")

while True:
    try:
        result = r.brpop("scans")
    except redis.exceptions.ConnectionError as exc:
        log.warning("Redis connection failed: %s. Retrying in 5 seconds.", exc)
        time.sleep(5)
        continue

    _, raw = result
    event = json.loads(raw)

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO scans (tracking_id, status, depot, scanned_at) VALUES (%s, %s, %s, %s)",
            (event["tracking_id"], event["status"], event["depot"], event["scanned_at"]),
        )
        cur.execute(
            """
            INSERT INTO parcels (tracking_id, status, updated_at) VALUES (%s, %s, %s)
            ON CONFLICT (tracking_id) DO UPDATE
            SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
            """,
            (event["tracking_id"], event["status"], event["scanned_at"]),
        )

    conn.commit()
    log.info("processed %s -> %s", event["tracking_id"], event["status"])
