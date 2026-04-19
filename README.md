# Tulip — Multi-User Bot

Prediction market maker bot — per-user sessions, slot-based strategy, admin controls.

---

## Architecture

```
multi_user_bot/
├── frontend/                  React/Vite/TypeScript  (port 3010)
├── backend/                   Python FastAPI          (port 8010)
└── polymarket_rust_service/   Rust/Axum CLOB engine  (port 8020)
```

| Service        | Port | Role                                  |
| -------------- | ---- | ------------------------------------- |
| Frontend       | 3010 | User + admin UI                       |
| Python backend | 8010 | Auth, sessions, DB, orchestration     |
| Rust service   | 8020 | Polymarket CLOB logic + order signing |

Auth: Privy only. Private keys never stored — all signing via Privy Server Wallets.

---

## Branch strategy

| Branch   | Purpose                                      |
| -------- | -------------------------------------------- |
| `main` | Production — deployed on Vultr VPS          |
| `dev`  | Development — tested locally before merging |

Never push directly to `main`. Merge `dev → main` only when ready to deploy.

```bash
# Daily dev workflow
git checkout dev
# ... make changes ...
git add -A && git commit -m "feat: xxx"
git push origin dev

# Deploy to prod
git checkout main
git merge dev
git push origin main
# Then SSH to VPS and pull (see Deployment section)
```

---

## Sensitive files — never committed

The following are in `.gitignore` and must **never** be pushed:

```
backend/.env
frontend/.env
polymarket_rust_service/.env
**/__pycache__/
**/node_modules/
**/target/          # Rust build artifacts (huge)
backend/venv/
backend/.venv/
*.pyc
*.pem
*.key
```

Copy the `.env.example` files to `.env` and fill in secrets locally.

---

## 1. Privy Setup

1. Create a project at [privy.io](https://privy.io)
2. **Settings → App:**
   - Copy **App ID** → `VITE_PRIVY_APP_ID` (frontend) + `PRIVY_APP_ID` (backend)
   - Copy **App Secret** → `PRIVY_APP_SECRET` (backend only)
3. **Settings → Basics → Verification key** — copy the ES256 public key (raw base64) → `PRIVY_VERIFICATION_KEY`
4. **Authorization Keys** (server-side signing without user presence):
   - **Settings → Authorization Keys → Generate key**
   - Copy **Key ID** → `PRIVY_AUTHORIZATION_KEY_ID`
   - Copy **Private key** (starts with `wallet-auth:`) → `PRIVY_AUTHORIZATION_KEY`

---

## 2. Database — SQL Editor (Supabase or self-hosted Postgres)

Create a project at [supabase.com](https://supabase.com), then paste in **SQL Editor → New query → Run**.
For self-hosted Postgres: `psql -U tulip -d tulip_prod -f schema.sql`

```sql
-- ============================================================
-- multi_user_bot — full schema
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- USERS
CREATE TABLE IF NOT EXISTS users (
    id                      TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    privy_user_id           TEXT UNIQUE,
    display_name            TEXT,
    display_name_updated_at TIMESTAMPTZ,
    is_admin                BOOLEAN NOT NULL DEFAULT FALSE,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    trading_volume          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    bot_pnl                 DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    fee_override_bps        INTEGER,
    effective_fee_bps       INTEGER NOT NULL DEFAULT 120,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_users_privy_user_id ON users (privy_user_id);

-- WALLETS
CREATE TABLE IF NOT EXISTS wallets (
    id                              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    user_id                         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    wallet_address                  TEXT NOT NULL,
    proxy_address                   TEXT,
    privy_wallet_id                 TEXT,
    is_primary                      BOOLEAN NOT NULL DEFAULT TRUE,
    clob_api_key                    TEXT,
    clob_api_secret                 TEXT,
    clob_api_passphrase             TEXT,
    onboarding_state                TEXT NOT NULL DEFAULT 'pending',
    onboarding_error_detail         TEXT,
    onboarding_complete             BOOLEAN NOT NULL DEFAULT FALSE,
    onboarding_completed_at         TIMESTAMPTZ,
    funding_state                   TEXT NOT NULL DEFAULT 'idle',
    funding_deposit_tx_hash         TEXT,
    funding_tx_hash                 TEXT,
    funding_deposit_address_cached  TEXT,
    funding_deposit_cached_at       TIMESTAMPTZ,
    funding_last_usdc_observed      DOUBLE PRECISION,
    funding_bridge_started_at       TIMESTAMPTZ,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_wallet_user_addr UNIQUE (user_id, wallet_address)
);
CREATE INDEX IF NOT EXISTS ix_wallets_user_id         ON wallets (user_id);
CREATE INDEX IF NOT EXISTS ix_wallets_privy_wallet_id ON wallets (privy_wallet_id);

-- STRATEGIES
CREATE TABLE IF NOT EXISTS strategies (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    name            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft',
    reserve_pct     DOUBLE PRECISION NOT NULL DEFAULT 5.0,
    fee_side        TEXT NOT NULL DEFAULT 'none',
    global_pause    BOOLEAN NOT NULL DEFAULT FALSE,
    current_major   INTEGER NOT NULL DEFAULT 1,
    current_minor   INTEGER NOT NULL DEFAULT 0,
    archived_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    strategy_id     TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    major           INTEGER NOT NULL,
    minor           INTEGER NOT NULL,
    version_label   TEXT NOT NULL,
    published_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_strategy_version UNIQUE (strategy_id, major, minor)
);
CREATE INDEX IF NOT EXISTS ix_strategy_versions_strategy_id ON strategy_versions (strategy_id);

CREATE TABLE IF NOT EXISTS strategy_slots (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    strategy_id     TEXT NOT NULL REFERENCES strategies(id) ON DELETE CASCADE,
    label           TEXT NOT NULL,
    allocation_pct  DOUBLE PRECISION NOT NULL DEFAULT 100.0,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    order_index     INTEGER NOT NULL DEFAULT 0,
    archived_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_strategy_slots_strategy_id ON strategy_slots (strategy_id);

CREATE TABLE IF NOT EXISTS strategy_slot_versions (
    id                    TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    strategy_version_id   TEXT REFERENCES strategy_versions(id) ON DELETE CASCADE,
    strategy_slot_id      TEXT NOT NULL REFERENCES strategy_slots(id) ON DELETE CASCADE,
    polymarket_url        TEXT,
    question              TEXT,
    condition_id          TEXT,
    token_id_yes          TEXT,
    token_id_no           TEXT,
    side                  TEXT DEFAULT 'YES',
    price_min             DOUBLE PRECISION,
    price_max             DOUBLE PRECISION,
    max_spread_ticks      INTEGER DEFAULT 1,
    sl_ticks              INTEGER DEFAULT 0,
    spread_from_ask       BOOLEAN DEFAULT FALSE,
    allocation_pct        DOUBLE PRECISION,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_strategy_slot_versions_version_id ON strategy_slot_versions (strategy_version_id);
CREATE INDEX IF NOT EXISTS ix_strategy_slot_versions_slot_id    ON strategy_slot_versions (strategy_slot_id);

-- SESSIONS
CREATE TABLE IF NOT EXISTS sessions (
    id                          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    user_id                     TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    wallet_id                   TEXT NOT NULL REFERENCES wallets(id) ON DELETE RESTRICT,
    strategy_id                 TEXT NOT NULL REFERENCES strategies(id) ON DELETE RESTRICT,
    start_strategy_version_id   TEXT REFERENCES strategy_versions(id),
    session_gross_capital       DOUBLE PRECISION NOT NULL,
    session_deployable_capital  DOUBLE PRECISION NOT NULL,
    session_fee_reserve         DOUBLE PRECISION NOT NULL,
    stop_mode                   TEXT NOT NULL,
    stop_days                   INTEGER,
    target_volume               DOUBLE PRECISION,
    session_sl_pct              DOUBLE PRECISION NOT NULL DEFAULT 20.0,
    status                      TEXT NOT NULL DEFAULT 'pending',
    health_state                TEXT NOT NULL DEFAULT 'starting',
    stop_reason                 TEXT,
    started_at                  TIMESTAMPTZ,
    stopped_at                  TIMESTAMPTZ,
    last_snapshot_at            TIMESTAMPTZ,
    recovery_deadline_at        TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_sessions_user_id    ON sessions (user_id);
CREATE INDEX IF NOT EXISTS ix_session_user_active ON sessions (user_id, status);

CREATE TABLE IF NOT EXISTS session_slots (
    id                                   TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    session_id                           TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    strategy_slot_id                     TEXT NOT NULL REFERENCES strategy_slots(id),
    current_strategy_slot_version_id     TEXT REFERENCES strategy_slot_versions(id),
    replacement_strategy_slot_version_id TEXT REFERENCES strategy_slot_versions(id),
    slot_state          TEXT NOT NULL DEFAULT 'idle',
    allocation_pct      DOUBLE PRECISION NOT NULL,
    allocated_capital   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    deployed_capital    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    volume              DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    realized_pnl        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    unrealized_pnl      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    paid_fees           DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_session_slots_session_id ON session_slots (session_id);

CREATE TABLE IF NOT EXISTS session_slot_market_history (
    id                                TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    session_slot_id                   TEXT NOT NULL REFERENCES session_slots(id) ON DELETE CASCADE,
    applied_strategy_version_id       TEXT REFERENCES strategy_versions(id),
    applied_strategy_slot_version_id  TEXT REFERENCES strategy_slot_versions(id),
    polymarket_url   TEXT NOT NULL,
    question         TEXT,
    condition_id     TEXT,
    token_id_yes     TEXT,
    token_id_no      TEXT,
    active_token_id  TEXT,
    side             TEXT,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at         TIMESTAMPTZ,
    close_reason     TEXT,
    volume           DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    realized_pnl     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    fees_paid        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_session_slot_market_history_slot_id ON session_slot_market_history (session_slot_id);

CREATE TABLE IF NOT EXISTS session_orders (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    session_slot_id TEXT NOT NULL REFERENCES session_slots(id) ON DELETE CASCADE,
    clob_order_id   TEXT NOT NULL,
    side            TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    price           DOUBLE PRECISION NOT NULL,
    size_usdc       DOUBLE PRECISION NOT NULL,
    size_tokens     DOUBLE PRECISION,
    status          TEXT NOT NULL DEFAULT 'open',
    fill_usdc       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    fill_tokens     DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_session_orders_slot_id ON session_orders (session_slot_id);
CREATE INDEX IF NOT EXISTS ix_session_orders_clob_id ON session_orders (clob_order_id);

CREATE TABLE IF NOT EXISTS session_lots (
    id                TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    session_slot_id   TEXT NOT NULL REFERENCES session_slots(id) ON DELETE CASCADE,
    lot_id            TEXT NOT NULL,
    entry_price       DOUBLE PRECISION NOT NULL,
    target_sell_price DOUBLE PRECISION NOT NULL,
    size_usdc         DOUBLE PRECISION NOT NULL,
    tokens            DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    entry_order_id    TEXT,
    exit_order_id     TEXT,
    status            TEXT NOT NULL DEFAULT 'open',
    pnl               DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    sell_price        DOUBLE PRECISION,
    timestamp_entry   TIMESTAMPTZ,
    timestamp_exit    TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_session_lots_slot_id ON session_lots (session_slot_id);
CREATE INDEX IF NOT EXISTS ix_session_lots_lot_id  ON session_lots (lot_id);

CREATE TABLE IF NOT EXISTS session_snapshots (
    id                      TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    session_id              TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    snapshot_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    gross_capital           DOUBLE PRECISION NOT NULL,
    deployable_capital      DOUBLE PRECISION NOT NULL,
    fee_reserve_remaining   DOUBLE PRECISION NOT NULL,
    deployed_capital        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    total_volume            DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    realized_pnl            DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    unrealized_pnl          DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    paid_fees               DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    health_state            TEXT NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_session_snapshots_session_id ON session_snapshots (session_id);

-- FUNDING
CREATE TABLE IF NOT EXISTS funding_transactions (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    wallet_id       TEXT NOT NULL REFERENCES wallets(id) ON DELETE CASCADE,
    amount_usdc     DOUBLE PRECISION NOT NULL,
    state           TEXT NOT NULL DEFAULT 'initiated',
    tx_hash         TEXT,
    source          TEXT NOT NULL DEFAULT 'auto',
    error_detail    TEXT,
    initiated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_funding_tx_wallet ON funding_transactions (wallet_id);

-- AUDIT
CREATE TABLE IF NOT EXISTS audit_logs (
    id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    actor_id    TEXT,
    actor_role  TEXT,
    action      TEXT NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_audit_entity ON audit_logs (entity_type, entity_id);
CREATE INDEX IF NOT EXISTS ix_audit_actor  ON audit_logs (actor_id);
```

Get `DATABASE_URL` from Supabase → **Project Settings → Database → Connection string → URI**.
Use port **6543** (session pooler) for asyncpg: `postgresql+asyncpg://...@...supabase.com:6543/postgres?ssl=require`

---

## 3. Local Development Setup

### Prerequisites

| Tool         | Version | Notes                                                    |
| ------------ | ------- | -------------------------------------------------------- |
| Python       | 3.11+   | python.org                                               |
| Node.js      | 18+     | nodejs.org                                               |
| Rust + cargo | stable  | rustup.rs —`stable-x86_64-pc-windows-msvc` on Windows |
| PostgreSQL   | 14+     | Supabase (recommended) or local                          |

### Clone and branch setup

```bash
git clone https://github.com/Jo13012/tulip.git
cd tulip
git checkout dev   # always develop on dev
```

### Backend

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# Fill in .env (see variables below)
uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload
```

`backend/.env` variables:

```env
DATABASE_URL=postgresql+asyncpg://...
HTTP_PORT=8010
CORS_ORIGINS=http://localhost:3010

PRIVY_APP_ID=
PRIVY_APP_SECRET=
PRIVY_VERIFICATION_KEY=
PRIVY_AUTHORIZATION_KEY=wallet-auth:...
PRIVY_AUTHORIZATION_KEY_ID=

DEV_AUTH_ENABLED=false

POLYMARKET_RUST_URL=http://127.0.0.1:8020
CLOB_HOST=https://clob.polymarket.com
GAMMA_HOST=https://gamma-api.polymarket.com
CHAIN_ID=137

POLYMARKET_BUILDER_API_KEY=
POLYMARKET_BUILDER_API_SECRET=
POLYMARKET_BUILDER_API_PASSPHRASE=

ARBITRUM_RPC_URLS=https://arb1.arbitrum.io/rpc
ARBITRUM_CHAIN_ID=42161
ARBITRUM_USDC_ADDRESS=0xaf88d065e77c8cC2239327C5EDb3A432268e5831
PLATFORM_FEE_WALLET=0x...

FUNDING_USDC_MIN=20.0
FUNDING_ETH_MIN=0.002
FEE_WALLET_USDC_MIN=3.0
FEE_WALLET_USDC_MIN_WARN=1.5
FEE_WALLET_USDC_STOP=0.5
BRIDGE_MIN_USDC=5.0
```

### Rust service

```bash
cd polymarket_rust_service
cp .env.example .env
# Fill in .env
cargo run
# First build: several minutes
```

`polymarket_rust_service/.env` variables:

```env
POLY_RUST_HOST=127.0.0.1
POLY_RUST_PORT=8020
CLOB_HOST=https://clob.polymarket.com
GAMMA_HOST=https://gamma-api.polymarket.com
PRIVY_APP_ID=
PRIVY_APP_SECRET=
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

`frontend/.env` variables:

```env
VITE_API_URL=http://localhost:8010
VITE_WS_URL=ws://localhost:8010
VITE_PRIVY_APP_ID=
```

### Start all at once (Windows)

```bat
start_dev.bat
```

### Ports

| Service      | URL                          |
| ------------ | ---------------------------- |
| Frontend     | http://localhost:3010        |
| Backend API  | http://localhost:8010        |
| API docs     | http://localhost:8010/docs   |
| Rust service | http://127.0.0.1:8020/health |

---

## 4. Admin Bootstrap

After first login via Privy, grant admin to your account:

```sql
UPDATE users SET is_admin = true WHERE privy_user_id = 'did:privy:xxxx';
-- or if you know your display_name:
UPDATE users SET is_admin = true WHERE display_name = 'yourname';
```

Log out and back in.

---

## 5. Production Deployment — Vultr VPS

### 5.1 Provision server

Vultr → **Create Server → Cloud Compute → Ubuntu 22.04 LTS → 2 vCPU / 4 GB RAM minimum.**

```bash
# As root on VPS
apt update && apt upgrade -y
apt install -y git curl build-essential postgresql postgresql-contrib nginx certbot python3-certbot-nginx python3.11 python3.11-venv

# Dedicated user
adduser tulip
usermod -aG sudo tulip
su - tulip
```

### 5.2 Rust toolchain

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env
```

### 5.3 PostgreSQL (skip if using Supabase)

```bash
sudo -u postgres psql
```

```sql
CREATE USER tulip WITH PASSWORD 'STRONG_PASS_HERE';
CREATE DATABASE tulip_prod OWNER tulip;
GRANT ALL PRIVILEGES ON DATABASE tulip_prod TO tulip;
\q
```

```bash
# Run schema
psql -U tulip -d tulip_prod < schema.sql
```

### 5.4 Clone repo

```bash
cd ~
git clone https://github.com/Jo13012/tulip.git
cd tulip
# prod always runs main
git checkout main
```

### 5.5 Build Rust service

```bash
cd ~/tulip/polymarket_rust_service
cp .env.example .env
# Fill in .env with prod values
cargo build --release
```

### 5.6 Backend

```bash
cd ~/tulip/backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in .env with prod values (DATABASE_URL, all Privy keys, etc.)
```

### 5.7 Frontend — build static

```bash
cd ~/tulip/frontend
npm install
cp .env.example .env
# Fill in .env with prod values (VITE_API_URL=https://VOTRE_DOMAINE, etc.)
npm run build
# Output: dist/
```

### 5.8 Systemd services

**`/etc/systemd/system/polymarket-rust.service`:**

```ini
[Unit]
Description=Polymarket Rust Service
After=network.target

[Service]
User=tulip
WorkingDirectory=/home/tulip/tulip/polymarket_rust_service
ExecStart=/home/tulip/tulip/polymarket_rust_service/target/release/polymarket_rust_service
Restart=always
RestartSec=5
EnvironmentFile=/home/tulip/tulip/polymarket_rust_service/.env

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/tulip-backend.service`:**

```ini
[Unit]
Description=Tulip FastAPI Backend
After=network.target postgresql.service polymarket-rust.service

[Service]
User=tulip
WorkingDirectory=/home/tulip/tulip/backend
ExecStart=/home/tulip/tulip/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 --workers 1
Restart=always
RestartSec=5
EnvironmentFile=/home/tulip/tulip/backend/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable polymarket-rust tulip-backend
sudo systemctl start polymarket-rust tulip-backend
sudo systemctl status polymarket-rust tulip-backend
```

### 5.9 Nginx

```bash
sudo nano /etc/nginx/sites-available/tulip
```

```nginx
server {
    listen 80;
    server_name YOUR_DOMAIN;

    root /home/tulip/tulip/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8010;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /ws {
        proxy_pass http://127.0.0.1:8010;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/tulip /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# SSL (Let's Encrypt)
sudo certbot --nginx -d YOUR_DOMAIN
```

### 5.10 Verify

```bash
curl https://YOUR_DOMAIN/api/v1/health
sudo journalctl -u tulip-backend -f
sudo journalctl -u polymarket-rust -f
```

---

## 6. Deploying Updates

### Local → GitHub

```bash
# Work on dev
git checkout dev
git add -A && git commit -m "feat: xxx"
git push origin dev

# When ready for prod
git checkout main
git merge dev
git push origin main
```

### Send secrets to VPS (only when keys change)

```bash
scp backend/.env.prod     tulip@YOUR_VPS_IP:~/tulip/backend/.env
scp frontend/.env.prod    tulip@YOUR_VPS_IP:~/tulip/frontend/.env
scp polymarket_rust_service/.env.prod  tulip@YOUR_VPS_IP:~/tulip/polymarket_rust_service/.env
```

### Pull and restart on VPS

```bash
ssh tulip@YOUR_VPS_IP
cd ~/tulip
git pull origin main
```

Then restart only what changed:

```bash
# Backend change
sudo systemctl restart tulip-backend

# Rust change
cd polymarket_rust_service && cargo build --release && cd ..
sudo systemctl restart polymarket-rust

# Frontend change
cd frontend && npm run build
# Nginx serves new dist/ immediately — no restart needed
```

### With live sessions running

Backend restart = **automatic session recovery** (~3–5s downtime).

1. Check active sessions: `GET /api/v1/admin/sessions`
2. Optional: `global_pause=true` via admin panel
3. `sudo systemctl restart tulip-backend`
4. Check `health_state` of sessions after restart
5. If session stays `degraded`: `POST /api/v1/admin/sessions/{id}/recovery/retry`
6. Re-enable: `global_pause=false`

---

## Auth Flow

1. User logs in via Privy (email / Google / Twitter / wallet)
2. Frontend calls `POST /api/v1/auth/sync` with Privy access token
3. Backend verifies ES256 token, upserts user + creates Privy server wallet
4. Wallet stored in DB — private key never stored, lives in Privy secure enclave
5. On session start, Rust service signs orders via Privy API (Authorization Key)
