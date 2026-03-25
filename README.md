# Kalshi Sports Trading Bot 📈

An AI-powered paper-trading bot for [Kalshi](https://kalshi.com) sports prediction markets.
Monitors NFL, NBA, MLS, and IPL Cricket markets, analyses news + signals with Claude, and executes Kelly-sized paper trades against a $1,000 virtual bankroll.

---

## Architecture

```
┌────────────────────────────────────────────────────────────┐
│                      kalshi.local                          │
│                                                            │
│  Browser → Traefik Ingress                                 │
│                │                                           │
│       ┌────────┴──────────┐                                │
│       ▼                   ▼                                │
│  Streamlit UI         FastAPI Backend                      │
│  (port 8501)          (port 8000)                          │
│       │                   │                                │
│       └──── HTTP ──────► API routes                        │
│                           │                                │
│              ┌────────────┼────────────┐                   │
│              ▼            ▼            ▼                   │
│         PostgreSQL   Kalshi API   Anthropic API             │
│         (trades,     (RSA auth)   (claude-opus-4-6)        │
│          signals,                                          │
│          portfolio)                                        │
└────────────────────────────────────────────────────────────┘
```

**Scan pipeline (every 12 h or on demand):**

1. Fetch open Kalshi markets → classify by sport (NFL / NBA / MLS / IPL)
2. Pull news headlines → compute sentiment score
3. Apply rule-based signals (public-bias, volume momentum)
4. Send all signals to Claude → structured `{trade, side, confidence, reasoning}`
5. Apply minimum confidence (0.55) + minimum edge (3%) thresholds
6. Kelly Criterion sizing (¼ Kelly, cap at 5% bankroll / $50)
7. Persist paper trade to PostgreSQL; deduct stake from virtual bankroll

> **Note:** The Helm chart and OpenTofu module live in the homelab infrastructure repo at
> `~/git-homelab/home-lab-infrastructure/charts/kalshi-trading/` and
> `~/git-homelab/home-lab-infrastructure/modules/kalshi-trading-bot/`.

---

## Quick Start — Local Dev (Docker Compose)

### Prerequisites
- Docker Desktop / Docker Engine + Compose v2
- An Anthropic API key

```bash
# 1. Clone / enter project
cd kalshi-trading-bot

# 2. Set up env
cp .env.example .env
# Edit .env — at minimum set ANTHROPIC_API_KEY=sk-ant-...

# 3. Build and run
docker compose up --build

# 4. Open the dashboard
open http://localhost:8501

# 5. Trigger a manual scan (optional)
curl -X POST http://localhost:8000/api/v1/scan
```

The FastAPI docs are at http://localhost:8000/docs.

---

## Build and Push Images to Docker Hub

```bash
docker login
./build-and-push.sh
```

Builds and pushes:
- `vkon2001/kalshi-trading-backend:latest` from `./backend/`
- `vkon2001/kalshi-trading-streamlit:latest` from `./streamlit_app/`

---

## Deploy to k3s via OpenTofu (Homelab Repo)

The Helm chart and OpenTofu module both live in the homelab infrastructure repo:

```
~/git-homelab/home-lab-infrastructure/
  charts/kalshi-trading/        ← Helm chart
  modules/kalshi-trading-bot/   ← OpenTofu module
```

### 1. Inject secrets via `terraform.tfvars`

In the homelab repo root, add to `terraform.tfvars` (gitignored — never commit real values):

```hcl
install-kalshi-trading-bot = true

anthropic_api_key  = "sk-ant-..."
kalshi_key_id      = "your-kalshi-key-id"
kalshi_private_key = "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
```

See `modules/kalshi-trading-bot/terraform.tfvars.example` for the template.

### 2. Apply

```bash
cd ~/git-homelab/home-lab-infrastructure

tofu init
tofu plan
tofu apply
```

This will:
- Create the `trading` namespace
- Create the `kalshi-secrets` Kubernetes Secret (Anthropic key, Kalshi RSA credentials)
- Deploy the Helm chart (backend, streamlit, PostgreSQL, Traefik ingress)

### 3. Add a hosts entry

```bash
# Get the Traefik LoadBalancer IP
kubectl get svc -n kube-system traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}'

# Add to /etc/hosts (replace x.x.x.x with the IP above)
echo "x.x.x.x  kalshi.local" | sudo tee -a /etc/hosts
```

Open http://kalshi.local in your browser.

### 4. Trigger a manual scan

The scheduler runs every 12 hours automatically. To scan immediately:

```bash
curl -X POST http://kalshi.local/api/v1/scan
```

Or from inside the cluster:

```bash
kubectl exec -n trading deployment/kalshi-backend -- \
  python3 -c "import httpx, asyncio; asyncio.run(httpx.AsyncClient().aclose()) or print(asyncio.run(httpx.AsyncClient(timeout=60).post('http://localhost:8000/api/v1/scan')).json())"
```

The response shows how many markets were scanned and trades placed:
```json
{"markets_scanned": 12, "trades_placed": 3, "timestamp": "2026-03-17T..."}
```

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET  | `/api/v1/health`      | Liveness + DB check |
| GET  | `/api/v1/portfolio`   | Balance, P&L, win rate |
| GET  | `/api/v1/trades`      | All trades (`?status=open&sport=NBA`) |
| POST | `/api/v1/trades/{id}/resolve` | Close a trade (`?outcome=win&exit_price=0.9`) |
| GET  | `/api/v1/markets`     | Monitored markets with signal strength |
| POST | `/api/v1/scan`        | Trigger an immediate market scan |
| GET  | `/api/v1/settings`    | Read current config |
| PATCH| `/api/v1/settings`    | Hot-patch config (in-memory) |

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | **Required.** Anthropic API key |
| `KALSHI_KEY_ID` | — | Kalshi API key ID (RSA auth) |
| `KALSHI_PRIVATE_KEY` | — | Kalshi RSA private key (PEM) |
| `NEWS_API_KEY` | — | Optional newsapi.org key (falls back to Google News RSS) |
| `PAPER_TRADING` | `true` | Set `false` to enable live trading |
| `INITIAL_BANKROLL` | `1000.0` | Starting virtual balance ($) |
| `KELLY_FRACTION` | `0.25` | Fractional Kelly multiplier |
| `MAX_TRADE_PCT` | `0.05` | Max trade as fraction of bankroll |
| `MAX_TRADE_USD` | `50.0` | Hard dollar cap per trade |
| `MIN_CONFIDENCE` | `0.55` | Minimum AI confidence to trade |
| `MIN_EDGE_THRESHOLD` | `0.03` | Minimum 3% edge required to trade |
| `SCAN_INTERVAL_HOURS` | `12` | Scheduler frequency |

---

## Switching from Paper Trading to Live Trading

1. Obtain a **Kalshi API key** (key ID + RSA private key) from your Kalshi account.
2. Update `terraform.tfvars` in the homelab repo with your live credentials.
3. Re-apply with `PAPER_TRADING=false`:
   ```bash
   tofu apply
   ```
4. Implement `KalshiClient.place_order()` in `backend/app/services/kalshi_client.py` using the [Kalshi REST API](https://trading-api.readme.io/reference/createorder).

> ⚠️ **Always test extensively in paper mode before going live.** Real money is at risk.

---

## Project Structure

```
kalshi-trading-bot/
├── backend/
│   ├── app/
│   │   ├── api/routes/       # REST endpoints
│   │   ├── core/             # config, database
│   │   ├── models/           # ORM models + Pydantic schemas
│   │   ├── services/         # Kalshi client, news, AI, trading, scanner
│   │   └── schedulers/       # APScheduler job
│   ├── Dockerfile
│   └── requirements.txt
├── streamlit_app/            # Streamlit dashboard (5 pages)
│   ├── pages/
│   └── Dockerfile
├── postgres/                 # DB init SQL
├── build-and-push.sh         # Build + push to vkon2001 on Docker Hub
├── docker-compose.yml        # Local dev stack
└── .env.example

# Helm chart + OpenTofu module live in the homelab repo:
# ~/git-homelab/home-lab-infrastructure/charts/kalshi-trading/
# ~/git-homelab/home-lab-infrastructure/modules/kalshi-trading-bot/
```
