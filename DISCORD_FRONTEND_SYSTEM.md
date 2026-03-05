# Toyoko Inn Alert Frontend - Technical Specification

## Overview
A Python-based Discord frontend service specification for watch management slash commands and backend webhook alerts delivered to Discord users.

This frontend is designed to follow the same bot structure pattern used in `ahuei123456/offkai-bot`:
- A `commands.Bot` subclass as the runtime entrypoint.
- Slash commands organized in cogs/extensions.
- Explicit command sync in `setup_hook`.
- Centralized configuration loading with validation.

## 1. System Architecture

### A. Discord Bot Runtime
- **Framework:** `discord.py` (`discord.ext.commands` + `app_commands`).
- **Interaction Model:** Slash commands only (no text-prefix command surface).
- **Startup Pattern:**
  - Load config.
  - Instantiate bot client subclass.
  - Load cogs/extensions.
  - Sync slash commands (guild-scoped in dev, global in production).
  - Start webhook receiver.

### B. Backend API Bridge
- **Target Backend:** Toyoko Alert API (`/watches`, `/status`) authenticated by `X-API-Key`.
- **Data Mapping:**
  - Discord user ID is mapped to backend `user_id` as `discord_<discord_user_id>`.
  - Frontend callback URL is registered as each watch's `callback_url`.
- **Transport:** `httpx` async client with timeouts and structured error handling.
- **Request Contract:** Use backend field names exactly as implemented (`hotel_code`, `checkin_date`, `checkout_date`, `num_people`, `smoking_type`, `room_type`, `user_id`, `callback_url`).

### C. Webhook Receiver
- **Framework:** FastAPI (lightweight receiver app within frontend service).
- **Endpoint:** `POST /notify`.
- **Purpose:** Accept backend events and route alerts to Discord users.
- **Delivery Behavior:**
  - Parse event payload.
  - Resolve target Discord user from `user_id`.
  - Send DM with Rich Embed and "Book Now" button.
  - Return HTTP status to control backend retry semantics (2xx for success/permanent failure, 5xx for retry).

## 2. Core Components

### A. Bot Entrypoint (`src/toyoko_inn_alert/discord_frontend/main.py`)
- Defines `ToyokoDiscordClient(commands.Bot)`.
- Implements `setup_hook()` for:
  - Extension loading.
  - Slash command sync.
  - Shared service initialization (backend client, webhook app/task).
- Defines global tree error handler for user-friendly ephemeral errors.

### B. Config Module (`src/toyoko_inn_alert/discord_frontend/config.py`)
- Loads JSON config (or environment fallback) and validates required keys.
- Exposes cached `get_config()` accessor.

### C. Slash Command Cogs (`src/toyoko_inn_alert/discord_frontend/cogs/`)
- `general.py`: basic utility commands (health/help).
- `watches.py`: watch lifecycle commands:
  - `/watch add`: Uses **Autocomplete** for hotel selection and flexible date parsing.
  - `/watch list`: Displays active watches with **Interactive Buttons** for removal.
  - `/watch remove`: Direct removal by ID.
- Commands are implemented with strict input validation and clear ephemeral responses.

### D. Backend Client (`src/toyoko_inn_alert/discord_frontend/backend_client.py`)
- Wraps backend REST calls with request/response normalization.

### E. Webhook API (`src/toyoko_inn_alert/discord_frontend/webhook_api.py`)
- Provides FastAPI app and `POST /notify`.
- Supports HMAC verification via `X-Toyoko-Signature`.
  - Signature header is always present and should be verified.
- Converts payload into **Discord Rich Embeds** with action buttons.

## 3. Slash Command Contract (Frontend UX)

### A. `/watch add`
- **Inputs:**
  - `hotel`: (String) **Autocomplete supported**. Fetched from `data/hotels.json`.
  - `checkin`: (String) **Flexible parsing** (e.g., "tomorrow", "2026-04-01").
  - `checkout`: (String) **Flexible parsing**.
  - `people`: (Integer) Default `1`.
  - `smoking`: (Choice) `noSmoking` or `smoking`.
  - `room_type`: (Choice) Placeholder (Single, Double, Twin, Triple+).
- **Behavior:**
  - Validate date order and value ranges.
  - Register watch via backend `POST /watches`.
- **Response:**
  - Ephemeral success message with created `watch_id`.
  - If backend rejects due to max active watches, show:
    - `"Oops, you already have 10 active watch requests. Remove one with /watch remove and try again."`
  - This message is triggered when backend returns machine-readable error code `MAX_ACTIVE_WATCHES`.

### B. `/watch list`
- **Behavior:**
  - Call backend `GET /watches/{user_id}`.
  - Render active watches in a **Rich Embed**.
  - **Interactive Removal:** Attach "Remove" buttons for each watch.
- **Response:** Interactive list or empty state message.

### C. `/watch remove`
- **Inputs:** `watch_id` (integer).
- **Behavior:** Call backend `DELETE /watches/{watch_id}`.
- **Response:** Confirmation or not-found message.

### D. `/toyoko status`
- **Behavior:** Calls backend `GET /status`.

## 4. Webhook Contract and Delivery Semantics

### A. Inbound Payload (From Backend)
- Expected payload aligns with backend contract in `docs/API_CONTRACT.md`.
- Event types currently emitted by backend are `AVAILABILITY_FOUND` and `INSTANT_HIT`.

### B. Alert Delivery (Discord UX)
- **Rich Embed:** Includes Hotel Code, Price, Dates, and Booking URL.
- **Room Details:** Include `stay.people`, `stay.smoking`, and `stay.room_type` when present.
- **Action Button:** A "Book Now" button linking directly to the Toyoko Inn reservation page (`booking_url`).
- **DM Reliability:** If DMs are blocked, the webhook receiver logs a "Permanent Failure" (2xx) to stop backend retries, and optionally warns the user if they interact with the bot later.

### C. Backend Error Mapping (Frontend Requirements)
- Backend should return machine-readable error codes so slash commands can show clear messages.
- Required mapping:
  - `INVALID_API_KEY` -> show configuration/authentication error and avoid retry spam.
  - `MAX_ACTIVE_WATCHES` -> show max-watch guidance message.
  - `DUPLICATE_WATCH` -> tell user the same watch already exists.
  - `WATCH_NOT_FOUND` -> tell user that watch ID no longer exists.
  - Unknown errors -> generic retry-later message.

## 5. Security and Operational Safeguards

### A. Secrets
- Discord bot token, Backend API key, Webhook signature secret.

### B. Abuse Controls
- Per-user command cooldowns for write actions.
- Input validation to prevent malformed or malicious payloads.

## 6. Development Roadmap

### Phase 1: Foundation
- [ ] Create frontend package structure and config loader.
- [ ] Implement bot entrypoint with slash-command sync.
- [ ] Add backend client wrapper.

### Phase 2: Watch Commands & UX
- [ ] Implement `/watch add` with **Hotel Autocomplete** and **Date Parsing**.
- [ ] Implement `/watch list` with **Interactive Removal Buttons**.
- [ ] Add user input validation and standardized error responses.

### Phase 3: Alert Ingestion & Presentation
- [ ] Implement `POST /notify` webhook endpoint.
- [ ] Implement **Rich Embed** formatting for alerts.
- [ ] Add **"Book Now" buttons** to notification messages.
- [ ] Add HMAC signature verification.

### Phase 4: Reliability and Deployment
- [ ] Add retry-aware response semantics (2xx vs 5xx).
- [ ] Add container/service definition for frontend runtime.
- [ ] Document reverse-proxy/public callback routing.

## 7. Testing Strategy

### Unit Tests (`tests/unit/discord_frontend/`)
- Config validation and fallback behavior.
- Backend client request shaping and header injection.
- User ID mapping (`discord_<id>` conversion).
- Webhook signature verification logic.
- Alert message formatting from payload samples.

### Integration Tests (`tests/integration/discord_frontend/`)
- Slash command handlers with mocked backend API.
- Webhook endpoint with mocked Discord user fetch/DM send.
- Error-path tests for retry semantics (`2xx` vs `5xx`).

### Mocking Policy
- Never call real Discord API in automated tests.
- Never call deployed backend in tests; use mocked `httpx` responses.

## 8. Coding Standards
- **Manager:** `uv`
- **Linter/Formatter:** `ruff`
- **Type Checker:** `pyrefly`
- **Testing:** `pytest`
- **Style Constraint:** Slash-command-first UX; cogs/extensions required.

## 9. Changelog

### [2026-03-05] - UX and Reliability Enhancements
- **Hotel Discovery:** Added requirement for **Slash Command Autocomplete** in `/watch add` to eliminate manual hotel code entry.
- **Flexible Dates:** Added support for **natural language date parsing** (e.g., "tomorrow", "next Friday") for check-in/check-out inputs.
- **Interactive Management:** Updated `/watch list` to use **Discord Buttons** for one-click watch removal.
- **Rich Notifications:** Specified the use of **Rich Embeds** and **Action Buttons** ("Book Now") for alert delivery.
- **Delivery Reliability:** Added explicit mapping for DM-blocked users to ensure backend retries are handled correctly (2xx for permanent failure).
- **Backend Error UX:** Added explicit frontend handling for backend code `MAX_ACTIVE_WATCHES` with user-facing guidance.
- **Signature Rollout:** Clarified strict signature verification requirements.
