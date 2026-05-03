"""
funding_service.py - Wallet funding + balance helpers (Arbitrum).

Uses persistent httpx clients (connection pooling) — no TCP handshake overhead per call.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import httpx

from app.core.config import settings

log = logging.getLogger("uvicorn.error")

# ── Persistent HTTP clients (module-level singletons) ────────────────────────
# One client per RPC host — keeps TCP+TLS connections alive across calls.
# Limits: 10 connections per host, 2s connect timeout, 6s read timeout.
_LIMITS = httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30)
_TIMEOUT = httpx.Timeout(connect=4.0, read=10.0, write=2.0, pool=1.0)

_arb_client: httpx.AsyncClient | None = None
_rust_client: httpx.AsyncClient | None = None


def _get_arb_client() -> httpx.AsyncClient:
    global _arb_client
    if _arb_client is None or _arb_client.is_closed:
        _arb_client = httpx.AsyncClient(timeout=_TIMEOUT, limits=_LIMITS, trust_env=False)
    return _arb_client


def get_rust_client() -> httpx.AsyncClient:
    """Shared client for Polymarket Rust service. Used by balance_poller too."""
    global _rust_client
    if _rust_client is None or _rust_client.is_closed:
        _rust_client = httpx.AsyncClient(
            base_url=settings.POLYMARKET_RUST_URL,
            timeout=httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=1.0),
            limits=httpx.Limits(max_connections=30, max_keepalive_connections=20, keepalive_expiry=30),
            trust_env=False,
        )
    return _rust_client


async def close_clients() -> None:
    """Call at app shutdown."""
    global _arb_client, _rust_client
    if _arb_client and not _arb_client.is_closed:
        await _arb_client.aclose()
    if _rust_client and not _rust_client.is_closed:
        await _rust_client.aclose()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _arb_rpc_urls() -> list[str]:
    return [u.strip() for u in settings.ARBITRUM_RPC_URLS.split(",") if u.strip()]


async def get_eth_balance(address: str) -> float | None:
    addr = address.strip()
    if not addr:
        return None
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_getBalance", "params": [addr, "latest"]}
    http = _get_arb_client()
    for rpc in _arb_rpc_urls():
        try:
            resp = await http.post(rpc, json=payload)
            hex_val = resp.json().get("result")
            if hex_val:
                return int(hex_val, 16) / 1e18
        except Exception:
            continue
    return None


async def _get_erc20_balance(address: str, token_address: str) -> float | None:
    addr = address.strip().lower().replace("0x", "")
    if not addr or not token_address:
        return None
    data = "0x70a08231" + addr.zfill(64)
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
               "params": [{"to": token_address, "data": data}, "latest"]}
    http = _get_arb_client()
    for rpc in _arb_rpc_urls():
        try:
            resp = await http.post(rpc, json=payload)
            hex_val = resp.json().get("result")
            if hex_val and hex_val != "0x":
                return int(hex_val, 16) / 1_000_000
        except Exception:
            continue
    return None


async def get_usdc_balance(address: str) -> float | None:
    return await _get_erc20_balance(address, settings.ARBITRUM_USDC_ADDRESS)


async def get_balances(address: str) -> tuple[float | None, float | None]:
    usdc, eth = await asyncio.gather(get_usdc_balance(address), get_eth_balance(address))
    return usdc, eth


async def get_balances_batch(addresses: list[str]) -> dict[str, tuple[float | None, float | None]]:
    """
    Fetch USDC + ETH balances for multiple addresses in a single JSON-RPC batch call.
    Returns {address: (usdc, eth)}.
    """
    if not addresses:
        return {}

    usdc_contract = settings.ARBITRUM_USDC_ADDRESS
    if not usdc_contract:
        # fallback: individual calls
        results = await asyncio.gather(*[get_balances(a) for a in addresses], return_exceptions=True)
        return {a: (r if isinstance(r, tuple) else (None, None)) for a, r in zip(addresses, results)}

    batch: list[dict] = []
    for i, addr in enumerate(addresses):
        clean = addr.strip().lower().replace("0x", "").zfill(40)
        batch.append({
            "jsonrpc": "2.0", "id": f"usdc_{i}",
            "method": "eth_call",
            "params": [{"to": usdc_contract, "data": "0x70a08231" + clean.zfill(64)}, "latest"],
        })
        batch.append({
            "jsonrpc": "2.0", "id": f"eth_{i}",
            "method": "eth_getBalance",
            "params": [addr.strip(), "latest"],
        })

    http = _get_arb_client()
    raw_results: list[dict] = []
    for rpc in _arb_rpc_urls():
        try:
            resp = await http.post(rpc, json=batch, timeout=10.0)
            if resp.is_success:
                raw_results = resp.json()
                break
        except Exception:
            continue

    # Parse results by id
    by_id: dict[str, str | None] = {}
    for item in raw_results:
        if isinstance(item, dict):
            by_id[str(item.get("id", ""))] = item.get("result")

    out: dict[str, tuple[float | None, float | None]] = {}
    for i, addr in enumerate(addresses):
        usdc_hex = by_id.get(f"usdc_{i}")
        eth_hex  = by_id.get(f"eth_{i}")
        usdc = (int(usdc_hex, 16) / 1_000_000) if usdc_hex and usdc_hex != "0x" else None
        eth  = (int(eth_hex, 16) / 1e18)        if eth_hex  and eth_hex  != "0x" else None
        out[addr] = (usdc, eth)
    return out


async def get_trading_balance(proxy_address: str) -> float | None:
    """Trading balance (USDC on Polymarket) via Rust service."""
    if not proxy_address:
        return None
    try:
        resp = await get_rust_client().get(f"/api/v1/wallet-balance/{proxy_address}")
        if resp.is_success:
            return resp.json().get("balance")
    except Exception:
        pass
    return None


def compute_reserve_target(usdc_balance: float = 0.0, trading_balance: float | None = None) -> float:
    """Reserve 1: USDC to keep on Arbitrum EOA.
    Dynamic: max(FEE_WALLET_USDC_MIN, trading_balance × FEE_BUFFER_PCT).
    """
    base = settings.FEE_WALLET_USDC_MIN
    total_capital = max(0.0, usdc_balance) + max(0.0, trading_balance or 0.0)
    if total_capital > 0:
        return max(base, total_capital * settings.FEE_BUFFER_PCT)
    return base


def compute_bridge_amount(usdc_balance: float, trading_balance: float | None = None) -> float:
    """Bridge everything above Reserve 1, minimum BRIDGE_MIN_USDC."""
    reserve_target = compute_reserve_target(usdc_balance, trading_balance)
    if usdc_balance <= 0:
        return 0.0
    bridge_amount = max(0.0, usdc_balance - reserve_target)
    if bridge_amount < settings.BRIDGE_MIN_USDC:
        return 0.0
    return float(Decimal(str(bridge_amount)).quantize(Decimal("0.000001")))


# Backwards-compat alias
MIN_BRIDGE_DELTA = 5.0  # kept for external references — use settings.BRIDGE_MIN_USDC at runtime


async def get_latest_usdc_deposit_tx(address: str, lookback_blocks: int = 5000) -> str | None:
    addr = address.strip().lower()
    if not addr:
        return None
    usdc = settings.ARBITRUM_USDC_ADDRESS
    if not usdc:
        return None

    topic0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    to_topic = "0x" + addr.replace("0x", "").rjust(64, "0")
    http = _get_arb_client()

    latest_block = None
    block_payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}
    for rpc in _arb_rpc_urls():
        try:
            resp = await http.post(rpc, json=block_payload)
            hex_val = resp.json().get("result")
            if hex_val:
                latest_block = int(hex_val, 16)
                break
        except Exception:
            continue
    if latest_block is None:
        return None

    from_block = max(0, latest_block - lookback_blocks)
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
               "params": [{"fromBlock": hex(from_block), "toBlock": "latest",
                           "address": usdc, "topics": [topic0, None, to_topic]}]}

    for rpc in _arb_rpc_urls():
        try:
            resp = await http.post(rpc, json=payload)
            logs = resp.json().get("result") or []
            if not isinstance(logs, list) or not logs:
                continue

            def _sort_key(log):
                return (int(log.get("blockNumber", "0x0"), 16),
                        int(log.get("logIndex", "0x0"), 16))

            return sorted(logs, key=_sort_key)[-1].get("transactionHash")
        except Exception:
            continue
    return None
