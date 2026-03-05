# Toyoko Inn Alert System

A high-performance Python backend for monitoring Toyoko Inn hotel availability.

## Quick Links
- [Technical Specification](SYSTEM.md): Internal architecture, API reverse engineering, and roadmap.
- [API Contract](docs/API_CONTRACT.md): Public documentation for frontend developers (e.g., Discord bot).
- [Deployment Guide](docs/DEPLOYMENT.md): Environment variables, Docker deployment, admin access, backup/restore, and post-deploy checks.

## Features
- **tRPC Reverse Engineered:** Optimized for minimal server overhead.
- **Event-Driven:** Uses a persistent SQLite queue for reliable alert delivery.
- **Robust:** Handles timezones (JST), retries with backoff, and bot-mimicry.

## Development
```powershell
# Setup environment
uv install

# Run tests
uv run pytest

# Run linter
uv run ruff check .
```

## API Key Management
This system requires a database-backed API Key for all requests.

### 1. Provision a new key
To grant access to a new frontend (e.g., a Discord bot):
```powershell
uv run scripts/manage_keys.py create --name "My Discord Bot"
```
**Important:** Copy the key immediately. It is stored hashed/securely and won't be shown again in plain text.

### 2. List existing keys
```powershell
uv run scripts/manage_keys.py list
```

## Running the Service
For deployment, Docker, admin credentials, and operations, use the Deployment Guide.

```powershell
# Local development
uv run python -m toyoko_inn_alert.main

# Using Docker
docker compose up --build
```
