import argparse
import secrets

from sqlmodel import Session, select

from toyoko_inn_alert.db import APIKey, create_db_and_tables, engine


def create_key(name: str):
    # Ensure tables exist
    create_db_and_tables()

    key = f"tk_{secrets.token_urlsafe(32)}"
    with Session(engine) as session:
        new_key = APIKey(key=key, client_name=name)
        session.add(new_key)
        session.commit()
        print(f"Created API Key for '{name}':")
        print(f"Key: {key}")
        print(
            "Keep this key secret! It will not be shown again in plain "
            "text if you lose it."
        )


def list_keys():
    with Session(engine) as session:
        keys = session.exec(select(APIKey)).all()
        for k in keys:
            status = "ACTIVE" if k.is_active else "REVOKED"
            print(
                f"ID: {k.id} | Client: {k.client_name} | "
                f"Status: {status} | Key prefix: {k.key[:10]}..."
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage Toyoko Inn Alert API Keys")
    subparsers = parser.add_argument_group("commands")

    parser.add_argument("command", choices=["create", "list"])
    parser.add_argument("--name", help="Name of the client (e.g. 'Discord Bot')")

    args = parser.parse_args()

    if args.command == "create":
        if not args.name:
            print("Error: --name is required for 'create'")
        else:
            create_key(args.name)
    elif args.command == "list":
        list_keys()
