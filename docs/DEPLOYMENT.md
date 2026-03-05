# Toyoko Inn Alert - Deployment Guide

This guide covers production-style deployment for the current backend implementation.

## 1. What You Are Deploying

- FastAPI service on port `8000`
- Background scheduler jobs (watch polling + notification delivery) running in-process
- SQLite database file `toyoko.db` for watches, notifications, and API keys
- Server-rendered admin panel at `/admin`

## 2. Required Environment Variables

- `ADMIN_USERNAME`: HTTP Basic username for `/admin`
- `ADMIN_PASSWORD`: HTTP Basic password for `/admin`

Optional:

- `TOYOKO_API_KEY`: present in `docker-compose.yml` but not used by current code
- `WEBHOOK_SIGNATURE_SECRET`: Secret key used to generate an HMAC-SHA256 signature for outgoing webhooks. Must be shared with clients to verify payloads.
- `PYTHONUNBUFFERED=1`: recommended for container logging
- `LOG_LEVEL`: logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`), default `INFO`

## 3. Deploy with Docker Compose (Recommended)

Compose reads a `.env` file from the project root (same directory as `docker-compose.yml`) for `${VAR}` substitution.
If you launch Compose from a different directory, use `--project-directory` or `--env-file`.

### Step 1: Set admin credentials

```powershell
$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD = "replace-with-strong-password"
```

If you use a `.env` file for Docker Compose, define:

```text
ADMIN_USERNAME=admin
ADMIN_PASSWORD=replace-with-strong-password
```

### Step 2: Build and start

```powershell
docker compose up --build -d
```

### Step 3: Validate service

```powershell
Invoke-WebRequest http://localhost:8000/status
```

Expected result includes `"status":"healthy"`.

### Step 4: Create an API key for clients

```powershell
uv run scripts/manage_keys.py create --name "Production Bot"
```

Store this key securely. Client calls must send `X-API-Key`.

## 4. Deploy Without Docker

### Step 1: Install dependencies

```powershell
uv sync --frozen
```

### Step 2: Set admin credentials

```powershell
$env:ADMIN_USERNAME = "admin"
$env:ADMIN_PASSWORD = "replace-with-strong-password"
```

### Step 3: Start service

```powershell
uv run python -m toyoko_inn_alert.main
```

## 5. Post-Deploy Checks

- API health: `GET /status`
- Admin login: `GET /admin`
- Create/list watches from API docs: `/docs`
- Confirm notification queue is processing (check `notification` table status changes)

## 6. Data Persistence and Backups

The default SQLite file is `toyoko.db`.

For Docker Compose in this repo:

- `./toyoko.db` on host is mounted into container at `/app/toyoko.db`
- `./data` on host is mounted into container at `/app/data`

Backup example:

```powershell
Copy-Item .\toyoko.db .\toyoko.db.bak
```

Restore example:

```powershell
Copy-Item .\toyoko.db.bak .\toyoko.db -Force
```

Stop writes before restore to avoid corruption.

## 7. Updating the Service

```powershell
docker compose down
docker compose up --build -d
```

If schema changes are introduced in future updates, add and run migrations before start-up.

## 8. Security Notes

- Do not keep default admin credentials.
- Put the API behind HTTPS (reverse proxy or managed ingress).
- Restrict admin path exposure (`/admin`) with network controls if possible.
- Rotate API keys periodically via admin panel or `scripts/manage_keys.py`.

### Webhook Signatures

To ensure that outbound webhook notifications are authentic, configure `WEBHOOK_SIGNATURE_SECRET`. The system computes an HMAC-SHA256 hash of the raw JSON payload and sends it in the `X-Toyoko-Signature` header.

Current behavior (as of 2026-03-05):
- Signature header is always present on outbound webhooks.
- Service startup fails if `WEBHOOK_SIGNATURE_SECRET` is missing.

**Verification Example (Python):**
```python
import hmac
import hashlib

def verify_signature(secret: str, raw_payload: bytes, signature_header: str) -> bool:
    expected_signature = hmac.new(
        secret.encode("utf-8"), raw_payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected_signature, signature_header)
```

**Secret Rotation Procedure:**
1. Generate a new strong secret and store it in your secret manager.
2. Update consumers to accept both old and new secrets during a short overlap window.
3. Update `WEBHOOK_SIGNATURE_SECRET` in deployment and restart service instances.
4. Confirm incoming webhook verification succeeds with the new secret.
5. Remove old secret support from consumers after all senders are confirmed rotated.

**Webhook Field Naming:**
1. Payload keys are snake_case: `user_id`, `stay.room_type`, and `booking_url`.
2. No legacy camelCase aliases are emitted.

## 9. Viewing Logs

For Docker Compose deployments:

```powershell
docker compose logs -f api
```

Recent logs:

```powershell
docker compose logs --since 24h api
```
