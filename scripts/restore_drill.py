"""Rehearse backup/restore on synthetic, disposable local PostgreSQL only."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from psycopg import sql


def fingerprints(conn):
    tables = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    ).fetchall()
    result = {}
    for (table,) in tables:
        query = sql.SQL(
            "SELECT row_to_json(t)::text AS row FROM {} t ORDER BY 1"
        ).format(sql.Identifier(table))
        rows = conn.execute(query).fetchall()
        digest = hashlib.sha256()
        for (row,) in rows:
            encoded = row.encode()
            digest.update(str(len(encoded)).encode() + b":" + encoded)
        result[table] = {"rows": len(rows), "sha256": digest.hexdigest()}
    sequences = conn.execute(
        "SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"
    ).fetchall()
    for (sequence,) in sequences:
        value = conn.execute(
            sql.SQL("SELECT last_value, is_called FROM {}").format(
                sql.Identifier(sequence)
            )
        ).fetchone()
        result[f"sequence:{sequence}"] = list(value)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        required=True,
        help="New wms_test_* database; existing targets are refused",
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/restore-drill"))
    args = parser.parse_args()
    url = os.environ.get("WMS_TEST_DATABASE_URL", "")
    parsed = urlparse(url)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise SystemExit("Only disposable local PostgreSQL is allowed.")
    source = parsed.path.removeprefix("/")
    if (
        not source.startswith("wms_test_")
        or not re.fullmatch(r"wms_test_[a-z0-9_]+", args.target)
        or source == args.target
    ):
        raise SystemExit("Source/target must be distinct wms_test_* databases.")
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / "synthetic.dump"
    if archive.exists():
        raise SystemExit("Choose a fresh output directory; existing archive preserved.")
    with psycopg.connect(url, autocommit=True) as conn:
        if conn.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (args.target,)
        ).fetchone():
            raise SystemExit("Target already exists; no database changed.")
        before = fingerprints(conn)
        if not before.get("warehouse_stock", {}).get("rows") or not before.get(
            "warehouse_order", {}
        ).get("rows"):
            raise SystemExit(
                "Seed representative synthetic stock and orders before the drill."
            )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(args.target)))
    # Credentials go to child-process environment, never command arguments or output.
    from urllib.parse import unquote

    env = dict(
        os.environ,
        PGHOST=parsed.hostname,
        PGPORT=str(parsed.port or 5432),
        PGUSER=unquote(parsed.username or ""),
        PGPASSWORD=unquote(parsed.password or ""),
        PGDATABASE=source,
        PGSSLMODE="disable",
    )
    subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--compress=0",
            "--no-owner",
            "--no-acl",
            "--file",
            str(archive),
        ],
        env=env,
        check=True,
    )
    subprocess.run(
        [
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            "--dbname",
            args.target,
            str(archive),
        ],
        env=env,
        check=True,
    )
    with psycopg.connect(url, dbname=args.target) as restored:
        after = fingerprints(restored)
    if before != after:
        raise SystemExit(
            "Restore mismatch. Preserve both synthetic databases for review."
        )
    report = {
        "result": "passed",
        "scope": "synthetic local PostgreSQL only",
        "tables": sum(not key.startswith("sequence:") for key in before),
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "fingerprints": after,
    }
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        f"Restore verified: {report['tables']} tables and all public sequence states match."
    )
    print(
        "Target retained for inspection. Render production backup restoration remains a separate gate."
    )


if __name__ == "__main__":
    main()
