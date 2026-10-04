"""Create the SQLite merchant registry and import the existing merchant CSV."""
from merchant_store import initialize_database, list_merchants


def main():
    initialize_database()
    merchants = list_merchants()
    print(f"SQLite registry ready: {len(merchants)} merchants")
    print("Demo locations are sample coordinates around Chengalpattu and Chennai.")


if __name__ == "__main__":
    main()