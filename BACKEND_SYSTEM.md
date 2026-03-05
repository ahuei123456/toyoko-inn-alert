# Toyoko Inn Alert System - Technical Specification

## Overview
A high-performance Python-based alert system that monitors Toyoko Inn hotel availability and prices using their internal tRPC API. It notifies users when "Sold Out" hotels become "Available."

## 1. API Architecture (Reverse Engineered)

### A. Availability & Prices
- **Endpoint:** `https://www.toyoko-inn.com/api/trpc/hotels.availabilities.prices`
- **Method:** `GET`
- **Protocol:** tRPC (Batched)
- **Key Inputs:** `hotelCodes` (Array), `checkinDate`, `checkoutDate`, `numberOfPeople`, `numberOfRoom`, `smokingType`.
- **Key Outputs:** `lowestPrice` (Int), `existEnoughVacantRooms` (Bool).
- **Behavior:** `lowestPrice: 0` and `existEnoughVacantRooms: false` indicates the hotel is sold out.

### B. Geographic Master Data
- **Endpoint:** `https://www.toyoko-inn.com/api/trpc/public.areas.list`
- **Purpose:** Maps `areaId` to Prefectures and Regions.

## 2. Core Components

### API Layer (`src/toyoko_inn_alert/api.py`)
- **Framework:** FastAPI.
- **Mandatory Validations:**
    - `hotelCode` exists in `hotels.json`.
    - `checkinDate` is today or in the future (JST).
    - `checkinDate < checkoutDate`.
    - Max active watches per `userId` is enforced (`10`).
- **Instant Hit Logic:** Upon successful `POST /watches`, the API performs an immediate check. If the hotel is already available, a notification is queued immediately.
- **Endpoints:**
    - `POST /watches`: Add a new hotel/date combination to monitor.
    - `GET /watches/{user_id}`: Retrieve active monitors for a specific user.
    - `DELETE /watches/{watch_id}`: Cancel a monitor.
    - `GET /status`: Health check and polling engine stats.

### Polling Engine (`src/toyoko_inn_alert/watcher.py`)
- **Type:** Background service (or `FastAPI` BackgroundTask/`Celery` for scale).
- **Strategy:** Group active watches by `(checkin_date, checkout_date, people, smoking)`.
  - *Note: `roomType` is ignored in grouping as it is currently a placeholder.*
- **Batching:** Chunk up to 10 `hotelCodes` per tRPC request.
- **Throttling:** Conservative polling (1–4 hours).

### Notifier (`src/toyoko_inn_alert/notifier/`)
- **Mechanism:** Pluggable Notification Sinks (Event-Driven).
- **Supported Sinks:**
    - **Local Queue (Default):** SQLite-based persistent table for zero-config reliability.
    - **Webhooks:** Outbound HTTP POST.
    - **External Queues:** Redis or RabbitMQ (optional for scaling).
- **Reliability:**
    - Local SQLite queue ensures no alerts are lost during bot downtime.
    - Decoupled "Producer" (Watcher) and "Consumer" (Bot API/Bridge).
- **Webhook Signing Requirement:**
    - Outbound webhook requests include `X-Toyoko-Signature` (HMAC-SHA256 of raw JSON payload).
    - Signing key is provided by environment variable (e.g. `WEBHOOK_SIGNATURE_SECRET`).

### Discovery Engine (`src/toyoko_inn_alert/discovery.py`)
...
## 3. Development Roadmap

### Phase 1: The Client (In Progress)
- [x] Reverse engineer tRPC URL encoding.
- [ ] Implement `ToyokoClient` with `httpx` for async requests.

### Phase 2: API & Persistence
- [ ] Set up SQLModel and SQLite.
- [ ] Implement FastAPI endpoints for Watch CRUD operations.
- [ ] Implement "Instant Hit" logic: immediate availability check during watch registration.

### Phase 3: Background Watcher
- [ ] Implement the async polling loop as a managed background service.
- [ ] Logic for detecting state transitions (`Sold Out` -> `Available`).

### Phase 4: Integration
- [ ] Implement the Webhook Callback system to notify external frontends.
- [ ] Add webhook signature header (`X-Toyoko-Signature`) in notifier.
- [ ] Add machine-readable API error codes for frontend UX mapping.
- [ ] Enforce max active watch limit per user (`10`) at API layer.

### Phase 5: Deployment & Containerization
- [ ] Create `Dockerfile` (optimized multi-stage build using `uv`).
- [ ] Create `docker-compose.yml` for local development and persistence.
- [ ] Implement container-based integration tests (verifying persistence and networking).
- [ ] Documentation for production deployment (environment variables, volumes).

## 4. Formal API Contract

### A. Inbound (REST API)
- **OpenAPI:** Automatically served at `/openapi.json`.
- **Auth:** All requests require `X-API-Key` header.
- **Business Limits:**
  - Maximum `10` active watches per `userId`.
- **`POST /watches` Request Body:**
  ```json
  {
    "hotelCode": "00088",
    "checkinDate": "2026-03-04",
    "checkoutDate": "2026-03-05",
    "numberOfPeople": 1,
    "smokingType": "noSmoking",
    "roomType": 10,
    "userId": "discord_12345",
    "callbackUrl": "https://bot.service/api/notify"
  }
  ```

### B. Outbound (Webhook Payload)
The frontend MUST implement an endpoint that accepts this POST body:
- **`POST {callbackUrl}` Request Body:**
  ```json
  {
    "event": "AVAILABILITY_FOUND",
    "timestamp": "2026-03-04T12:00:00Z",
    "userId": "discord_12345",
    "hotel": {
      "code": "00088",
      "name": "Toyoko INN Kitami Ekimae",
      "price": 6498
    },
    "stay": {
      "checkin": "2026-03-04",
      "checkout": "2026-03-05"
    },
    "bookingUrl": "https://www.toyoko-inn.com/search/result/room_plan/..."
  }
  ```

### C. Security
- **Webhook Verification:** Outbound payloads include `X-Toyoko-Signature` (HMAC-SHA256) for the frontend to verify authenticity.
- **Signing Secret:** Backend must use configured secret key (e.g. `WEBHOOK_SIGNATURE_SECRET`) and should fail fast on startup if signature mode is required but secret is missing.
- **Rollout Status:** Signature behavior is currently optional; this is a temporary compatibility mode and should be monitored closely until strict mode is enforced.

### D. Error Contract (Required for Frontend UX)
- `POST /watches` duplicate watch:
  - `409 Conflict`
  - `detail.code = "DUPLICATE_WATCH"`
- `POST /watches` max active watches reached:
  - `409 Conflict`
  - `detail.code = "MAX_ACTIVE_WATCHES"`
  - `detail.message = "You can only have up to 10 active watches."`
- Invalid request parameters:
  - `400` or `422` with stable `detail.code` where possible.

## 5. Operational Safeguards

### Timezone Management
- **Standard:** All internal logic MUST use `Asia/Tokyo` (JST).
- **Date Handling:** Conversions between ISO strings and API dates must account for the JST offset to prevent "off-by-one" errors during the Japan date rollover.

### Rate Limiting & Anti-Bot
- **Global Budget:** The `ToyokoClient` implements a strict concurrency limit and a maximum of `N` requests per hour across all watches.
- **Browser Mimicry:** Rotate `User-Agent` and include standard headers (`Referer`, `Origin`) to match browser behavior.

### Maintenance
- **Watch Cleanup:** Automatic daily task to delete/archive watches for past stay dates.
- **Queue Pruning:** Expire undeliverable notifications after 24 hours.

## 6. Debugging & Testing (Headless)

- **Interactive API Docs:** Use FastAPI's `/docs` (Swagger) to manually manage watches and trigger actions.
- **Database Inspection:** Monitor `toyoko.db` directly to verify state transitions and queue growth.
- **Admin Endpoints:** Implement `POST /admin/run-polling` to bypass timers during development.
- **Mock Consumer:** Create `scripts/mock_consumer.py` to simulate the Discord bot's behavior by pulling from the SQLite queue.

## 7. Testing Strategy

### Unit Tests (`tests/unit/`)
- **`ToyokoClient`**: Verify correct tRPC URL encoding/decoding and date formatting.
- **Models**: Ensure Pydantic models correctly handle "Sold Out" states (price=0) and transport variations in `AccessInfo`.
- **Logic**: Test the "hit" detection logic (comparing `last_available` vs `new_available`).

### Integration Tests (`tests/integration/`)
- **Database**: Verify `Watch` CRUD operations and persistence using a temporary SQLite database.
- **API**: Use `httpx.ASGITransport` to test FastAPI endpoints (adding/removing watches).
- **Watcher Loop**: End-to-end test of the polling cycle using `respx` or `pytest-mock` to simulate Toyoko Inn API responses.
- **Reliable Delivery**: Verify that failed notifications are correctly persisted and retried.

### Mocking Policy
- **External APIs**: NEVER hit the real Toyoko Inn API during automated tests. Use `respx` to mock tRPC responses.
- **Time**: Use `freezegun` to test time-sensitive polling logic and notification expiration.

### Additional Required Tests (Discord Frontend Integration)
- API test for `MAX_ACTIVE_WATCHES` rejection after the 10th active watch for one `userId`.
- API test for duplicate-watch rejection returning `DUPLICATE_WATCH`.
- Notifier test that `X-Toyoko-Signature` is present and verifiable.
- Notifier test ensuring payload schema remains compatible with frontend embed rendering.

## 8. Coding Standards
- **Manager:** `uv` for dependencies.
- **Linter/Formatter:** `ruff`.
- **Type Checker:** `pyrefly`.
- **Testing:** `pytest` (mocking tRPC responses is mandatory).

## 9. Required Work for Discord Frontend Integration (2026-03-05)

### Priority 1 (Blockers)
- [ ] Emit `X-Toyoko-Signature` from notifier on every webhook POST.
- [ ] Add configurable `WEBHOOK_SIGNATURE_SECRET` for signing.
- [ ] Enforce max 10 active watches per `userId` in `POST /watches`.
- [ ] Return machine-readable error code `MAX_ACTIVE_WATCHES` when limit is exceeded.
- [ ] Make max-watch enforcement concurrency-safe (prevent race conditions under parallel `POST /watches` requests).
- [ ] Fix date-range error code mismatch by standardizing on `INVALID_DATE_RANGE` in implementation and docs.

### Priority 2 (Strongly Recommended)
- [ ] Return machine-readable error code `DUPLICATE_WATCH` for duplicate creation attempts.
- [ ] Keep stable error payload shape for frontend mapping (`detail.code`, `detail.message`).
- [ ] Expand webhook `stay` payload with `people`, `smoking`, `roomType` when available to improve alert display fidelity.

### Priority 3 (Operational)
- [ ] Document signature rollout and secret rotation in deployment docs.
- [ ] Add regression tests for the error contract and signature behavior.
- [ ] Plan and execute migration from optional signature mode to strict signature enforcement.
