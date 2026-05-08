"""
redeem_service.py — Redeem winning CTF tokens via Safe.
- Binary markets: via Polymarket relayer (PRECHECK validates CTF balance directly)
- Neg-risk markets: direct RPC execTransaction (relayer PRECHECK fails on NegRisk-wrapped tokens)
"""
from __future__ import annotations

import logging

import httpx
from eth_abi import encode as abi_encode
from eth_utils import to_checksum_address, keccak

from app.core.config import settings
from app.services.polymarket_onboarding_service import (
    CTF,
    ZERO_ADDRESS,
    CHAIN_ID,
    _relayer_post_authed,
    _get_safe_nonce,
    _sign_safe_tx,
    _hash_safe_tx,
    _poll_transaction,
)
from app.services.privy_wallet_service import privy_wallet_service

log = logging.getLogger("redeem")

REDEEM_SELECTOR = bytes.fromhex("01b7037c")  # CTF.redeemPositions(address,bytes32,bytes32,uint256[])

PUSD     = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
USDC_E   = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
NEG_RISK = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"
POLYGON_RPC_FALLBACK = "https://rpc.ankr.com/polygon"

_NEG_RISK_REDEEM_SEL = keccak(b"redeemPositions(bytes32,uint256[])")[:4]
_SET_APPROVAL_SEL    = bytes.fromhex("a22cb465")  # setApprovalForAll(address,bool)


# ── On-chain helpers ───────────────────────────────────────────────────────────

async def _rpc_call(http: httpx.AsyncClient, method: str, params: list) -> str | None:
    for rpc in [POLYGON_RPC, POLYGON_RPC_FALLBACK]:
        try:
            r = await http.post(rpc, json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1}, timeout=6.0)
            result = r.json().get("result")
            if result is not None:
                return result
        except Exception:
            continue
    return None


async def _erc1155_balance(http: httpx.AsyncClient, contract: str, owner: str, token_id: int) -> int:
    data = "0x00fdd58e" + abi_encode(["address", "uint256"], [to_checksum_address(owner), token_id]).hex()
    result = await _rpc_call(http, "eth_call", [{"to": contract, "data": data}, "latest"])
    return int(result, 16) if result and result not in ("0x", "") else 0


async def _is_approved_for_all(http: httpx.AsyncClient, owner: str, operator: str) -> bool:
    data = "0xe985e9c5" + abi_encode(["address", "address"], [to_checksum_address(owner), to_checksum_address(operator)]).hex()
    result = await _rpc_call(http, "eth_call", [{"to": CTF, "data": data}, "latest"])
    return bool(int(result, 16)) if result and result not in ("0x", "") else False


async def _wait_tx(http: httpx.AsyncClient, tx_hash: str, polls: int = 30) -> dict | None:
    import asyncio
    for _ in range(polls):
        result = await _rpc_call(http, "eth_getTransactionReceipt", [tx_hash])
        if result and result != "null" and isinstance(result, dict):
            return result
        await asyncio.sleep(3)
    return None


# ── Encoders ──────────────────────────────────────────────────────────────────

def _encode_binary_redeem(condition_id: str, collateral: str) -> str:
    cid = bytes.fromhex(condition_id.lstrip("0x").zfill(64))
    encoded = abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [to_checksum_address(collateral), bytes(32), cid, [1, 2]],
    )
    return "0x" + REDEEM_SELECTOR.hex() + encoded.hex()


def _encode_neg_risk_redeem(condition_id: str, amount: int) -> str:
    cid = bytes.fromhex(condition_id.lstrip("0x").zfill(64))
    encoded = abi_encode(["bytes32", "uint256[]"], [cid, [amount, amount]])
    return "0x" + _NEG_RISK_REDEEM_SEL.hex() + encoded.hex()


def _encode_set_approval_for_all(operator: str) -> str:
    return "0x" + _SET_APPROVAL_SEL.hex() + bytes.fromhex(operator[2:].lower().zfill(64)).hex() + (1).to_bytes(32, "big").hex()


# ── Safe exec via relayer ──────────────────────────────────────────────────────

async def _relayer_exec(
    http: httpx.AsyncClient,
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
    to: str,
    calldata: str,
    nonce: str,
) -> dict | None:
    sig = await _sign_safe_tx(privy_wallet_id, safe_address, to, calldata, "0", 0, nonce)
    body = {
        "from": eoa_address, "to": to, "proxyWallet": safe_address,
        "data": calldata, "nonce": nonce, "signature": sig,
        "signatureParams": {
            "gasPrice": "0", "operation": "0",
            "safeTxnGas": "0", "baseGas": "0",
            "gasToken": ZERO_ADDRESS, "refundReceiver": ZERO_ADDRESS,
        },
        "type": "SAFE",
    }
    try:
        resp = await _relayer_post_authed(http, "/submit", body)
    except Exception as e:
        log.warning("relayer submit err: %s", e)
        return None
    tx_id = resp.get("transactionID")
    if not tx_id:
        log.warning("redeem no tx_id resp=%s", str(resp)[:120])
        return None
    return await _poll_transaction(http, tx_id, max_polls=30)


# ── Safe exec direct RPC (for neg-risk — relayer PRECHECK rejects NegRisk tokens) ──

async def _direct_exec(
    http: httpx.AsyncClient,
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
    to: str,
    calldata: str,
    nonce: str,
) -> str | None:
    """
    Execute Safe.execTransaction() via Privy eth_sendTransaction on Polygon.
    Used for neg-risk redeem — Polymarket relayer PRECHECK_SKIPs NegRisk-wrapped tokens.
    Returns tx_hash or None.
    """
    from app.services.privy_tx_service import _privy_rpc, PrivyTxError

    safe_tx_hash = _hash_safe_tx(safe_address, to, calldata, "0", 0, nonce)
    prefixed = keccak(b"\x19Ethereum Signed Message:\n32" + safe_tx_hash)
    raw_sig = await privy_wallet_service.secp256k1_sign(privy_wallet_id, "0x" + prefixed.hex())

    # Pack v: 27/28 → 31/32 (Safe eth_sign path)
    sig_bytes = bytes.fromhex(raw_sig[2:] if raw_sig.startswith("0x") else raw_sig)
    r, s, v = sig_bytes[:32], sig_bytes[32:64], sig_bytes[64]
    if v in (0, 1):   v += 31
    elif v in (27, 28): v += 4
    packed_sig = r + s + v.to_bytes(1, "big")

    calldata_bytes = bytes.fromhex(calldata[2:] if calldata.startswith("0x") else calldata)

    # execTransaction(to,value,data,operation,safeTxGas,baseGas,gasPrice,gasToken,refundReceiver,signatures)
    exec_data = bytes.fromhex("6a761202") + abi_encode(
        ["address", "uint256", "bytes", "uint8", "uint256", "uint256", "uint256", "address", "address", "bytes"],
        [to_checksum_address(to), 0, calldata_bytes, 0, 0, 0, 0,
         to_checksum_address(ZERO_ADDRESS), to_checksum_address(ZERO_ADDRESS), packed_sig],
    )

    try:
        result = await _privy_rpc(privy_wallet_id, {
            "method": "eth_sendTransaction",
            "caip2": f"eip155:{CHAIN_ID}",
            "params": {
                "transaction": {
                    "from": eoa_address,
                    "to": safe_address,
                    "data": "0x" + exec_data.hex(),
                    "value": "0x0",
                }
            },
        })
    except PrivyTxError as e:
        log.error("direct_exec: privy rpc error: %s", e)
        return None

    data = result.get("data") or {}
    tx_hash = data.get("hash") or data.get("txHash") or result.get("result") or result.get("hash")
    if not tx_hash:
        log.error("direct_exec: no tx_hash in response: %s", str(result)[:200])
        return None

    log.info("direct_exec: submitted tx=%s", tx_hash)
    receipt = await _wait_tx(http, tx_hash)
    if not receipt:
        log.error("direct_exec: receipt timeout tx=%s", tx_hash)
        return None
    status = receipt.get("status")
    ok = (int(status, 16) == 1) if isinstance(status, str) else bool(status)
    if ok:
        log.info("direct_exec: MINED tx=%s", tx_hash)
        return tx_hash
    log.error("direct_exec: REVERTED tx=%s", tx_hash)
    return None


# ── Main entry ────────────────────────────────────────────────────────────────

async def redeem_positions(
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
    condition_id: str,
    estimated_tokens: float = 0.0,
    known_token_id: str | None = None,
    outcome: str = "",
    nonce: str | None = None,
) -> dict:
    result: dict = {"ok": False, "tx_hash": None, "condition_id": condition_id,
                    "estimated_usdc": round(estimated_tokens, 2), "error": None}

    try:
        pid = int(known_token_id) if known_token_id else None
    except (ValueError, TypeError):
        pid = None

    log.info("redeem cond=%s tok=%s size=%.2f", condition_id[:16], (known_token_id or "?")[:16], estimated_tokens)

    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as http:

            # ── Detect market type via on-chain balance ────────────────────────
            neg_risk = False
            if pid:
                neg_bal = await _erc1155_balance(http, NEG_RISK, safe_address, pid)
                ctf_bal = await _erc1155_balance(http, CTF,      safe_address, pid)
                neg_risk = neg_bal > 0
                log.info("redeem bal: neg_risk_bal=%d ctf_bal=%d → neg_risk=%s", neg_bal, ctf_bal, neg_risk)
                if neg_bal == 0 and ctf_bal == 0:
                    result["error"] = "zero balance on both CTF and NegRisk — already redeemed or dust"
                    log.warning("redeem skip: zero balance cond=%s", condition_id[:16])
                    return result

            if neg_risk:
                # Direct RPC — relayer PRECHECK rejects NegRisk-wrapped tokens
                # Requires EOA to have POL for gas
                pol_bal_hex = await _rpc_call(http, "eth_getBalance", [eoa_address, "latest"])
                pol_wei = int(pol_bal_hex, 16) if pol_bal_hex and pol_bal_hex not in ("0x", "") else 0
                pol = pol_wei / 1e18
                if pol < 0.01:
                    result["error"] = f"insufficient POL for gas: eoa={eoa_address[:12]} pol={pol:.6f}"
                    log.error("redeem neg-risk: %s", result["error"])
                    return result

                amount = int(estimated_tokens * 1_000_000)
                safe_nonce = nonce or await _get_safe_nonce(http, safe_address)

                if not await _is_approved_for_all(http, safe_address, NEG_RISK):
                    log.info("redeem: missing NEG_RISK approval, adding cond=%s", condition_id[:16])
                    approval_hash = await _direct_exec(
                        http, privy_wallet_id, eoa_address, safe_address,
                        CTF, _encode_set_approval_for_all(NEG_RISK), safe_nonce,
                    )
                    if approval_hash:
                        safe_nonce = await _get_safe_nonce(http, safe_address)

                calldata = _encode_neg_risk_redeem(condition_id, amount)
                tx_hash = await _direct_exec(
                    http, privy_wallet_id, eoa_address, safe_address,
                    NEG_RISK, calldata, safe_nonce,
                )
                if tx_hash:
                    result["ok"] = True
                    result["tx_hash"] = tx_hash
                else:
                    result["error"] = "neg-risk direct exec failed"

            else:
                # Binary — via relayer (PRECHECK works on CTF balance)
                cur_nonce = nonce or await _get_safe_nonce(http, safe_address)
                mined = await _relayer_exec(
                    http, privy_wallet_id, eoa_address, safe_address,
                    CTF, _encode_binary_redeem(condition_id, PUSD), cur_nonce,
                )
                if not mined:
                    log.info("redeem binary pUSD failed, trying USDC.e cond=%s", condition_id[:16])
                    cur_nonce = await _get_safe_nonce(http, safe_address)
                    mined = await _relayer_exec(
                        http, privy_wallet_id, eoa_address, safe_address,
                        CTF, _encode_binary_redeem(condition_id, USDC_E), cur_nonce,
                    )
                if mined:
                    result["ok"] = True
                    result["tx_hash"] = mined.get("transactionHash")
                else:
                    result["error"] = "binary relayer failed on pUSD and USDC.e"

            if result["ok"]:
                log.info("redeem OK tx=%s size=%.2f", result["tx_hash"], estimated_tokens)
            else:
                log.error("redeem FAIL cond=%s err=%s", condition_id[:16], result["error"])

    except Exception as e:
        result["error"] = str(e)
        log.exception("redeem EXCEPTION cond=%s", condition_id[:16])

    return result
