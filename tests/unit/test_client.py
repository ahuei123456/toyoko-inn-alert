from datetime import datetime

import httpx
import pytest
import respx

from toyoko_inn_alert.client import ToyokoClient
from toyoko_inn_alert.models import PriceResult


@pytest.mark.asyncio
async def test_fetch_prices_success():
    client = ToyokoClient()
    checkin = datetime(2026, 3, 4)
    checkout = datetime(2026, 3, 5)
    hotel_codes = ["00088"]

    mock_response = [
        {
            "result": {
                "data": {
                    "json": {
                        "prices": {
                            "00088": {
                                "lowestPrice": 6498,
                                "existEnoughVacantRooms": True,
                                "isUnderMaintenance": False,
                            }
                        }
                    }
                }
            }
        }
    ]

    with respx.mock:
        route = respx.get(url__regex=r".*hotels\.availabilities\.prices.*").mock(
            return_value=httpx.Response(200, json=mock_response)
        )

        result = await client.fetch_prices(hotel_codes, checkin, checkout)

        assert isinstance(result, PriceResult)
        assert "00088" in result.prices
        assert result.prices["00088"].lowestPrice == 6498
        assert result.prices["00088"].existEnoughVacantRooms is True
        assert route.called


@pytest.mark.asyncio
async def test_fetch_prices_sold_out():
    client = ToyokoClient()
    checkin = datetime(2026, 3, 4)
    checkout = datetime(2026, 3, 5)
    hotel_codes = ["00003"]

    mock_response = [
        {
            "result": {
                "data": {
                    "json": {
                        "prices": {
                            "00003": {
                                "lowestPrice": 0,
                                "existEnoughVacantRooms": False,
                                "isUnderMaintenance": False,
                            }
                        }
                    }
                }
            }
        }
    ]

    with respx.mock:
        respx.get(url__regex=r".*hotels\.availabilities\.prices.*").mock(
            return_value=httpx.Response(200, json=mock_response)
        )

        result = await client.fetch_prices(hotel_codes, checkin, checkout)

        assert result.prices["00003"].lowestPrice == 0
        assert result.prices["00003"].existEnoughVacantRooms is False
