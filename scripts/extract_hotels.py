import json
from decimal import Decimal
from pathlib import Path

import ijson


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def extract_hotels():
    input_file = Path("data/result.json")
    output_file = Path("data/hotels.json")

    # Ensure data directory exists
    output_file.parent.mkdir(parents=True, exist_ok=True)

    current_hotels = []
    current_outside_hotels = []

    # Using ijson to stream through the large JSON file
    with input_file.open("rb") as f:
        # ijson.items() is more efficient for extracting specific lists
        f.seek(0)
        try:
            hotels_iter = ijson.items(f, "pageProps.searchResponse.hotels.item")
            for hotel in hotels_iter:
                current_hotels.append(hotel)
        except ijson.common.IncompleteJSONError:
            pass

        f.seek(0)
        try:
            # Shorten line to satisfy Ruff E501
            path = "pageProps.searchResponse.outsideRangeHotels.item"
            outside_hotels_iter = ijson.items(f, path)
            for hotel in outside_hotels_iter:
                current_outside_hotels.append(hotel)
        except ijson.common.IncompleteJSONError:
            pass

    all_hotels = current_hotels + current_outside_hotels

    with output_file.open("w", encoding="utf-8") as out:
        json.dump(all_hotels, out, indent=2, ensure_ascii=False, cls=DecimalEncoder)

    print(f"Extracted {len(all_hotels)} hotels to {output_file}")


if __name__ == "__main__":
    extract_hotels()
