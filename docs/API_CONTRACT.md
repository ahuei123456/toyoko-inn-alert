# Toyoko Inn Alert System - API Contract v1.1

This document defines the formal interface between the Toyoko Inn Alert Backend and any consuming frontends (Discord Bots, Web Dashboards, etc.).

## 1. Authentication
All requests to the Backend API must include an API Key.
- **Header:** `X-API-Key: <your_secret_key>`

## 2. Inbound API (REST)

### POST /watches
Add a new hotel to the monitor list.

#### Validation Rules
- **Hotel Existence:** `hotelCode` must be a valid 5-digit Toyoko Inn code (as found in `hotels.json`).
- **Timeline:**
  - `checkinDate` MUST NOT be in the past (JST).
  - `checkinDate` MUST be at least 1 day before `checkoutDate`.
  - Max stay duration: 14 days.
  - Max lead time: 12 months in the future.
  - **Deduplication:**
 Multiple identical watches (Hotel + Dates + People + Smoking) for the same `userId` are rejected.
- **Limits:** Max 10 active watches per `userId`.
- **Safety:** `callbackUrl` must be a valid HTTPS URL (no internal IPs).
- **Data Types:**
  - `numberOfPeople`: 1-4.
  - `smokingType`: `smoking` | `noSmoking`.
  - `roomType`: `10` (Single), `20` (Double), `30` (Twin), `40` (Triple+).
    - *Note: This field is currently a placeholder and does not affect search results.*

- **Body (JSON):**
```json
{
  "hotelCode": "00088",
  "checkinDate": "2026-03-04",
  "checkoutDate": "2026-03-05",
  "numberOfPeople": 1,
  "smokingType": "noSmoking",
  "roomType": 10,
  "userId": "unique_user_id",
  "callbackUrl": "https://your-service.com/api/callback"
}
```

#### Rejection Semantics (Required)
- Duplicate watch for same `userId` and parameters:
  - **HTTP:** `409 Conflict`
  - **Code:** `DUPLICATE_WATCH`
- Max active watches reached for `userId`:
  - **HTTP:** `409 Conflict`
  - **Code:** `MAX_ACTIVE_WATCHES`
  - **Message:** `"You can only have up to 10 active watches."`

### GET /watches/{user_id}
List all active monitors for a user.

### DELETE /watches/{watch_id}
Stop monitoring a specific watch.

---

## 3. Outbound Webhooks (The "Alert")
When the backend detects availability, it will POST to the `callbackUrl` provided during registration.

### Webhook Verification
The backend signs the payload so you can verify it came from us.
- **Header:** `X-Toyoko-Signature: <hmac_sha256_hash>`
- **Algorithm:** HMAC-SHA256 over raw JSON body bytes.
- **Secret:** Shared secret configured on backend (for example `WEBHOOK_SIGNATURE_SECRET`) and frontend verifier.

### Webhook Payload (JSON)
```json
{
  "event": "AVAILABILITY_FOUND",
  "timestamp": "2026-03-04T12:00:00Z",
  "userId": "unique_user_id",
  "hotel": {
    "code": "00088",
    "name": "Toyoko INN Kitami Ekimae",
    "price": 6498
  },
  "stay": {
    "checkin": "2026-03-04",
    "checkout": "2026-03-05",
    "people": 1,
    "smoking": "noSmoking",
    "roomType": 10
  },
  "bookingUrl": "https://www.toyoko-inn.com/search/result/room_plan/?hotel=00088&start=2026-03-04&end=2026-03-05&room=1&people=1&smoking=noSmoking&roomType=10"
}
```

## 4. Error Handling
- **401 Unauthorized:** Missing or invalid `X-API-Key`.
- **409 Conflict:** Business-rule rejection (for example `DUPLICATE_WATCH`, `MAX_ACTIVE_WATCHES`).
- **422 Unprocessable Entity:** Invalid date format or missing fields.
- **429 Too Many Requests:** Frontend is exceeding the API rate limit.

### Error Response Shape (Machine-Readable)
Frontend clients should parse a stable error code for user-facing messaging.

```json
{
  "detail": {
    "code": "MAX_ACTIVE_WATCHES",
    "message": "You can only have up to 10 active watches."
  }
}
```

#### Known Error Codes
- `MAX_ACTIVE_WATCHES`: User already has 10 active watches.
- `DUPLICATE_WATCH`: Same watch already exists for this user.
- `INVALID_HOTEL_CODE`: Unknown hotel code.
- `INVALID_DATE_RANGE`: `checkinDate` is not before `checkoutDate`.
