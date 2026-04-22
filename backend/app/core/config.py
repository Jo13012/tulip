"""
config.py — Central settings for multi_user_bot backend.
All config comes from environment variables / .env file.
"""
from __future__ import annotations
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Server
    HTTP_PORT: int = 8010
    CORS_ORIGINS: str = "http://localhost:3010"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://polybot:polybot@localhost:5432/polybot_multi"

    # Privy (production)
    PRIVY_APP_ID: str = ""
    PRIVY_APP_SECRET: str = ""
    PRIVY_VERIFICATION_KEY: str = ""           # ES256 public key (raw base64 or PEM)
    PRIVY_AUTHORIZATION_KEY: str = ""          # wallet-auth:<PKCS8 DER base64> — server-side signing
    PRIVY_AUTHORIZATION_KEY_ID: str = ""       # Key ID from Privy dashboard

    # Dev bypass auth (local development only — disable in production)
    DEV_AUTH_ENABLED: bool = False
    DEV_JWT_SECRET: str = "dev_only_secret_change_in_production"
    DEV_ADMIN_EMAIL: str = "admin@dev.local"
    DEV_USER_EMAIL: str = "user@dev.local"

    # Polymarket
    CLOB_HOST: str = "https://clob.polymarket.com"
    GAMMA_HOST: str = "https://gamma-api.polymarket.com"
    DATA_API_HOST: str = "https://data-api.polymarket.com"
    CHAIN_ID: int = 137
    POLYMARKET_RUST_URL: str = "http://127.0.0.1:8020"
    POLYMARKET_RELAYER_URL: str = "https://relayer-v2.polymarket.com"
    POLYMARKET_BRIDGE_URL: str = "https://bridge.polymarket.com"

    # Builder credentials (platform-level, used to deploy Safe for each user)
    POLYMARKET_BUILDER_API_KEY: str = ""
    POLYMARKET_BUILDER_SECRET: str = ""
    POLYMARKET_BUILDER_PASSPHRASE: str = ""

    # Platform fee wallet
    PLATFORM_FEE_WALLET: str = "0x0000000000000000000000000000000000000000"

    # Funding (Arbitrum + USDC)
    FUNDING_USDC_MIN: float = 20.0
    FUNDING_ETH_MIN: float = 0.002
    FEE_WALLET_USDC_MIN: float = 3.0          # Bridge keep target: keep $3 on EOA after bridge
    FEE_WALLET_USDC_MIN_WARN: float = 1.5     # Warn threshold
    FEE_WALLET_USDC_STOP: float = 0.5         # Session stop threshold
    FEE_WALLET_ETH_MIN: float = 0.001
    FEE_WALLET_ETH_MIN_WARN: float = 0.0005
    FEE_BUFFER_PCT: float = 0.05              # Reserve 1 dynamic: max($3, 5% of total capital)
    BRIDGE_MIN_USDC: float = 5.0              # Minimum bridgeable amount
    ARB_BALANCE_CHECK_INTERVAL: int = 60      # Seconds between Arbitrum balance checks in session engine
    ARBITRUM_CHAIN_ID: int = 42161
    ARBITRUM_RPC_URLS: str = "https://arb1.arbitrum.io/rpc,https://rpc.ankr.com/arbitrum,https://1rpc.io/arb"
    ARBITRUM_USDC_ADDRESS: str = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
