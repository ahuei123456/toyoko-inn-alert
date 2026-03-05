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

## 9. Viewing Logs

For Docker Compose deployments:

```powershell
docker compose logs -f api
```

Recent logs:

```powershell
docker compose logs --since 24h api
```
