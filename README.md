# Kalshi Sentinel

Kalshi-first, news-driven event-market intelligence + trading assistant.

## Goals
- Ingest Kalshi event/market data (demo or production)
- Generate explainable signals (edge vs market)
- Track outcomes + PnL (paper mode first)
- Local dashboard
- Optional: guarded trade execution later

## Safety Defaults
- Start in DEMO or read-only mode
- No live trading until explicit enablement + guardrails

## Local Setup

### 1) Backend
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../config/.env.example ../config/.env
python ../scripts/init_db.py
python app.py
```

Backend runs at: http://127.0.0.1:8099

### 2) Frontend
```bash
cd frontend
npm install
npm run dev
```

## Configuration
Put secrets in `config/.env` (gitignored). Do NOT paste private keys into chat.

- `KALSHI_ENV=demo|prod`
- `KALSHI_KEY_ID=...` (UUID)
- `KALSHI_PRIVATE_KEY_PATH=...` (path to PEM file)
- `KALSHI_BASE_URL=` optional override

## Notes
Kalshi demo API root: https://demo-api.kalshi.co/trade-api/v2
