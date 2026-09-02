from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from database import create_database_engine, session_factory
from postgres_runtime import PostgresAccountStore
from readiness import DatabaseReadiness


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision the first owner for a private Salamandra deployment."
    )
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--organization-name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()

    database_url = os.environ.get("SALAMANDRA_DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("SALAMANDRA_DATABASE_URL is required.")
    password = getpass.getpass("Temporary owner password: ")
    confirmation = getpass.getpass("Confirm temporary owner password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    if len(password) < 12:
        raise SystemExit("The temporary owner password must be at least 12 characters.")

    engine = create_database_engine(database_url, production=True)
    try:
        DatabaseReadiness(engine, ROOT).require_ready()
        accounts = PostgresAccountStore(session_factory(engine))
        account = accounts.create_user(
            name=args.name,
            email=args.email,
            password=password,
            role="owner",
            organization_id=args.organization_id,
            organization_name=args.organization_name,
        )
    finally:
        engine.dispose()
    print(f"Created owner account {account.email} for {account.organization_name}.")


if __name__ == "__main__":
    main()
