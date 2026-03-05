import json
import logging
import urllib.parse
from datetime import datetime
from time import perf_counter

import httpx

from toyoko_inn_alert.models import PriceResult

logger = logging.getLogger("toyoko.client")


class ToyokoClient:
    BASE_URL = "https://www.toyoko-inn.com/api/trpc"

    def __init__(self, timeout: float = 10.0):
        # Shorten User-Agent to avoid E501
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.toyoko-inn.com/",
            "Origin": "https://www.toyoko-inn.com",
        }
        self.timeout = timeout

    async def fetch_prices(
        self,
        hotel_codes: list[str],
        checkin_date: datetime,
        checkout_date: datetime,
        num_people: int = 1,
        num_rooms: int = 1,
        smoking_type: str = "noSmoking",
    ) -> PriceResult:
        """
        Fetches prices and availability for a list of hotels using the tRPC API.
        """
        # Format dates to ISO strings with Z suffix (UTC)
        checkin_str = checkin_date.strftime("%Y-%m-%dT06:00:00.000Z")
        checkout_str = checkout_date.strftime("%Y-%m-%dT06:00:00.000Z")

        input_data = {
            "0": {
                "json": {
                    "hotelCodes": hotel_codes,
                    "checkinDate": checkin_str,
                    "checkoutDate": checkout_str,
                    "numberOfPeople": num_people,
                    "numberOfRoom": num_rooms,
                    "smokingType": smoking_type,
                },
                "meta": {
                    "values": {
                        "checkinDate": ["Date"],
                        "checkoutDate": ["Date"],
                    }
                },
            }
        }

        encoded_input = urllib.parse.quote(json.dumps(input_data))
        path = "/hotels.availabilities.prices?batch=1&input="
        url = f"{self.BASE_URL}{path}{encoded_input}"
        start = perf_counter()

        async with httpx.AsyncClient(
            headers=self.headers, timeout=self.timeout
        ) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except Exception as e:
                elapsed_ms = int((perf_counter() - start) * 1000)
                logger.exception(
                    "toyoko_prices_request_failed hotel_count=%d elapsed_ms=%d "
                    "error=%s",
                    len(hotel_codes),
                    elapsed_ms,
                    e,
                )
                raise

            data = response.json()
            # tRPC batch response is a list
            result = data[0]["result"]["data"]["json"]
            elapsed_ms = int((perf_counter() - start) * 1000)
            logger.info(
                "toyoko_prices_request_ok hotel_count=%d status_code=%d elapsed_ms=%d",
                len(hotel_codes),
                response.status_code,
                elapsed_ms,
            )
            return PriceResult.model_validate(result)

    async def fetch_areas(self) -> dict:
        """
        Fetches the master list of geographic areas.
        """
        input_data = {"0": {"json": {}}}
        encoded_input = urllib.parse.quote(json.dumps(input_data))
        path = "/public.areas.list?batch=1&input="
        url = f"{self.BASE_URL}{path}{encoded_input}"
        start = perf_counter()

        async with httpx.AsyncClient(
            headers=self.headers, timeout=self.timeout
        ) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except Exception as e:
                elapsed_ms = int((perf_counter() - start) * 1000)
                logger.exception(
                    "toyoko_areas_request_failed elapsed_ms=%d error=%s",
                    elapsed_ms,
                    e,
                )
                raise

            elapsed_ms = int((perf_counter() - start) * 1000)
            logger.info(
                "toyoko_areas_request_ok status_code=%d elapsed_ms=%d",
                response.status_code,
                elapsed_ms,
            )
            return response.json()
