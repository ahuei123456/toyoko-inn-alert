import json
from pathlib import Path

from toyoko_inn_alert.models import Hotel


def load_hotels(file_path: str | Path) -> dict[str, Hotel]:
    """
    Reads a JSON file of hotels and returns a dictionary
    mapping hotelCode to Hotel objects.
    """
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Hotel data file not found at: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return {item["hotelCode"]: Hotel.model_validate(item) for item in data}
