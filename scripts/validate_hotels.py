import json
from pathlib import Path

from toyoko_inn_alert.models import Hotel


def validate_hotels():
    hotels_file = Path("data/hotels.json")
    if not hotels_file.exists():
        print(f"File not found: {hotels_file}")
        return

    with hotels_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"Loading {len(data)} hotels...")

    try:
        hotels = [Hotel.model_validate(item) for item in data]
        print(f"Successfully validated all {len(hotels)} hotels!")

        # Print first hotel as example
        if hotels:
            print("\nExample Hotel:")
            print(f"Name: {hotels[0].name}")
            print(f"Code: {hotels[0].hotelCode}")
            print(f"Station: {hotels[0].accessInfo.station}")

    except Exception as e:
        print(f"Validation failed: {e}")


if __name__ == "__main__":
    validate_hotels()
