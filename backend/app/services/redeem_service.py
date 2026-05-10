"""
redeem_service.py — Redeem winning CTF tokens via Safe.
- Binary markets: via Polymarket relayer (PRECHECK validates CTF balance directly)
  → if USDC.e collateral: wrap USDC.e → pUSD after redeem (CollateralOnramp)
- Neg-risk markets: direct RPC execTransaction (relayer PRECHECK fails on NegRisk-wrapped tokens)
  → batched via Safe Multisend to save POL gas
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

PUSD              = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
USDC_E            = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
NEG_RISK          = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"
SAFE_MULTISEND    = "0xA238CBeb142c10Ef7Ad8442C6D1f9E89e07e7761"

POLYGON_RPC          = "https://polygon-bor-rpc.publicnode.com"
POLYGON_RPC_FALLBACK = "https://rpc.ankr.com/polygon"

_NEG_RISK_REDEEM_SEL = keccak(b"redeemPositions(bytes32,uint256[])")[:4]
_SET_APPROVAL_SEL    = bytes.fromhex("a22cb465")  # setApprovalForAll(address,bool)
_APPROVE_SEL         = bytes.fromhex("095ea7b3")  # approve(address,uint256)
_WRAP_SEL            = keccak(b"wrap(address,address,uint256)")[:4]
_MULTISEND_SEL       = keccak(b"multiSend(bytes)")[:4]


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


async def _erc20_balance(http: httpx.AsyncClient, token: str, owner: str) -> int:
    data = "0x70a08231" + abi_encode(["address"], [to_checksum_address(owner)]).hex()
    result = await _rpc_call(http, "eth_call", [{"to": token, "data": data}, "latest"])
    return int(result, 16) if result and result not in ("0x", "") else 0


async def _is_approved_for_all(http: httpx.AsyncClient, owner: str, operator: str) -> bool:
    data = "0xe985e9c5" + abi_encode(["address", "address"], [to_checksum_address(owner), to_checksum_address(operator)]).hex()
    result = await _rpc_call(http, "eth_call", [{"to": CTF, "data": data}, "latest"])
    return bool(int(result, 16)) if result and result not in ("0x", "") else False


async def _get_nonce_onchain(http: httpx.AsyncClient, safe_address: str) -> str:
    result = await _rpc_call(http, "eth_call", [{"to": safe_address, "data": "0xaffed0e0"}, "latest"])
    return str(int(result, 16)) if result and result not in ("0x", "") else "0"


async def _get_eoa_tx_count_pending(http: httpx.AsyncClient, eoa_address: str) -> int:
    """EOA tx count including pending mempool — reflects Privy's internal nonce state."""
    result = await _rpc_call(http, "eth_getTransactionCount", [eoa_address, "pending"])
    return int(result, 16) if result and result not in ("0x", "") else 0


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


def _encode_neg_risk_redeem(condition_id: str, amount: int, outcome: str = "") -> str:
    cid = bytes.fromhex(condition_id.lstrip("0x").zfill(64))
    # NegRiskAdapter indexSet: index 0 = NO, index 1 = YES
    if outcome.upper() in ("YES", "Y"):
        amounts = [0, amount]
    else:
        amounts = [amount, 0]
    encoded = abi_encode(["bytes32", "uint256[]"], [cid, amounts])
    return "0x" + _NEG_RISK_REDEEM_SEL.hex() + encoded.hex()


def _encode_set_approval_for_all(operator: str) -> str:
    return "0x" + _SET_APPROVAL_SEL.hex() + bytes.fromhex(operator[2:].lower().zfill(64)).hex() + (1).to_bytes(32, "big").hex()


def _encode_approve(spender: str, amount: int) -> str:
    return "0x" + _APPROVE_SEL.hex() + abi_encode(["address", "uint256"], [to_checksum_address(spender), amount]).hex()


def _encode_wrap(asset: str, recipient: str, amount: int) -> str:
    return "0x" + _WRAP_SEL.hex() + abi_encode(
        ["address", "address", "uint256"],
        [to_checksum_address(asset), to_checksum_address(recipient), amount],
    ).hex()


def _encode_multisend(calls: list[tuple[str, str]]) -> str:
    """
    Encode list of (to, calldata) into Safe Multisend packed bytes.
    Each tx: operation(1) + to(20) + value(32) + dataLen(32) + data
    """
    packed = b""
    for to, data in calls:
        data_bytes = bytes.fromhex(data[2:] if data.startswith("0x") else data)
        packed += (
            b"\x00"                              # operation = CALL
            + bytes.fromhex(to_checksum_address(to)[2:])
            + (0).to_bytes(32, "big")            # value
            + len(data_bytes).to_bytes(32, "big")
            + data_bytes
        )
    return "0x" + _MULTISEND_SEL.hex() + abi_encode(["bytes"], [packed]).hex()


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


# ── Safe exec direct RPC ───────────────────────────────────────────────────────

async def _direct_exec(
    http: httpx.AsyncClient,
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
    to: str,
    calldata: str,
    operation: int = 0,
    nonce: str | None = None,
) -> str | None:
    """
    Execute Safe.execTransaction() via Privy on Polygon.
    Pass nonce explicitly when sending sequential txs to avoid GS013 collisions.
    """
    from app.services.privy_tx_service import _privy_rpc, PrivyTxError

    if nonce is None:
        nonce = await _get_nonce_onchain(http, safe_address)
    safe_tx_hash = _hash_safe_tx(safe_address, to, calldata, "0", operation, nonce)
    prefixed = keccak(b"\x19Ethereum Signed Message:\n32" + safe_tx_hash)
    raw_sig = await privy_wallet_service.secp256k1_sign(privy_wallet_id, "0x" + prefixed.hex())

    sig_bytes = bytes.fromhex(raw_sig[2:] if raw_sig.startswith("0x") else raw_sig)
    r, s, v = sig_bytes[:32], sig_bytes[32:64], sig_bytes[64]
    if v in (0, 1):     v += 31
    elif v in (27, 28): v += 4
    packed_sig = r + s + v.to_bytes(1, "big")

    calldata_bytes = bytes.fromhex(calldata[2:] if calldata.startswith("0x") else calldata)
    exec_data = bytes.fromhex("6a761202") + abi_encode(
        ["address", "uint256", "bytes", "uint8", "uint256", "uint256", "uint256", "address", "address", "bytes"],
        [to_checksum_address(to), 0, calldata_bytes, operation, 0, 0, 0,
         to_checksum_address(ZERO_ADDRESS), to_checksum_address(ZERO_ADDRESS), packed_sig],
    )

    try:
        result = await _privy_rpc(privy_wallet_id, {
            "method": "eth_sendTransaction",
            "caip2": f"eip155:{CHAIN_ID}",
            "params": {"transaction": {
                "from": eoa_address,
                "to": safe_address,
                "data": "0x" + exec_data.hex(),
                "value": "0x0",
            }},
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


# ── Wrap USDC.e → pUSD via relayer ────────────────────────────────────────────

async def _erc20_allowance(http: httpx.AsyncClient, token: str, owner: str, spender: str) -> int:
    data = "0xdd62ed3e" + abi_encode(
        ["address", "address"],
        [to_checksum_address(owner), to_checksum_address(spender)],
    ).hex()
    result = await _rpc_call(http, "eth_call", [{"to": token, "data": data}, "latest"])
    return int(result, 16) if result and result not in ("0x", "") else 0


async def _wrap_usdce_to_pusd(
    http: httpx.AsyncClient,
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
) -> bool:
    """
    Wrap all USDC.e in Safe to pUSD via CollateralOnramp.
    Uses _direct_exec (real tx receipt) — relayer marks tx mined even on ExecutionFailure.
    """
    balance = await _erc20_balance(http, USDC_E, safe_address)
    if balance < 1_000_000:  # < $1 USDC.e — skip
        return True

    log.info("wrap_usdce: balance=%d wrapping to pUSD safe=%s", balance, safe_address[:12])

    # tx1: approve CollateralOnramp to spend USDC.e
    approve_cd = _encode_approve(COLLATERAL_ONRAMP, balance)
    h1 = await _direct_exec(http, privy_wallet_id, eoa_address, safe_address, USDC_E, approve_cd)
    if not h1:
        log.error("wrap_usdce: approve tx failed")
        return False

    # Verify allowance was actually set
    allowance = await _erc20_allowance(http, USDC_E, safe_address, COLLATERAL_ONRAMP)
    if allowance < balance:
        log.error("wrap_usdce: approve mined but allowance=%d < balance=%d — inner exec failed", allowance, balance)
        return False
    log.info("wrap_usdce: approve OK allowance=%d", allowance)

    # tx2: wrap — nonce read fresh inside _direct_exec
    wrap_cd = _encode_wrap(USDC_E, safe_address, balance)
    h2 = await _direct_exec(http, privy_wallet_id, eoa_address, safe_address, COLLATERAL_ONRAMP, wrap_cd)
    if not h2:
        log.error("wrap_usdce: wrap tx failed")
        return False

    # Verify USDC.e balance actually dropped
    new_balance = await _erc20_balance(http, USDC_E, safe_address)
    if new_balance >= balance:
        log.error("wrap_usdce: wrap mined but USDC.e balance unchanged=%d — inner exec failed", new_balance)
        return False

    log.info("wrap_usdce: OK wrapped %d USDC.e → pUSD tx=%s", balance, h2)
    return True


# ── Batch neg-risk redeems via Multisend ──────────────────────────────────────

async def redeem_all_neg_risk(
    privy_wallet_id: str,
    eoa_address: str,
    safe_address: str,
    positions: list[dict],  # [{condition_id, token_id, size, outcome}, ...]
) -> dict:
    """
    Batch all neg-risk redeems into a single Safe Multisend tx (one POL gas fee).
    positions: list of dicts with condition_id, token_id, size (float tokens), outcome.
    Returns {"ok": bool, "tx_hash": str|None, "redeemed": int, "error": str|None}
    """
    result: dict = {"ok": False, "tx_hash": None, "redeemed": 0, "error": None}

    if not positions:
        result["ok"] = True
        return result

    try:
        async with httpx.AsyncClient(timeout=60.0, trust_env=False) as http:

            # POL check
            pol_hex = await _rpc_call(http, "eth_getBalance", [eoa_address, "latest"])
            pol = int(pol_hex, 16) / 1e18 if pol_hex and pol_hex not in ("0x", "") else 0
            if pol < 0.01:
                result["error"] = f"insufficient POL: {pol:.6f}"
                log.error("batch_neg_risk: %s", result["error"])
                return result

            # Ensure CTF approval for NegRiskAdapter (single tx if missing)
            if not await _is_approved_for_all(http, safe_address, NEG_RISK):
                log.info("batch_neg_risk: setting NEG_RISK approval")
                h = await _direct_exec(http, privy_wallet_id, eoa_address, safe_address,
                                       CTF, _encode_set_approval_for_all(NEG_RISK))
                if not h:
                    result["error"] = "failed to set NEG_RISK approval"
                    return result

            # Build Multisend calls
            calls: list[tuple[str, str]] = []
            for pos in positions:
                amount = int(float(pos["size"]) * 1_000_000)
                if amount == 0:
                    continue
                cd = _encode_neg_risk_redeem(pos["condition_id"], amount, pos.get("outcome", ""))
                calls.append((NEG_RISK, cd))
                log.info("batch_neg_risk: queued cond=%s amount=%d outcome=%s",
                         pos["condition_id"][:16], amount, pos.get("outcome", ""))

            if not calls:
                result["ok"] = True
                return result

            # Sequential direct exec — Multisend delegatecall blocked by Safe guard
            # Use relayer nonce (not on-chain) — Privy tracks relayer nonce internally,
            # and GS026 fires when we send a nonce lower than what Privy expects.
            relayer_nonce = int(await _get_safe_nonce(http, safe_address))
            onchain_nonce = int(await _get_nonce_onchain(http, safe_address))
            seq_nonce = max(relayer_nonce, onchain_nonce)
            log.info("batch_neg_risk: relayer_nonce=%d onchain_nonce=%d using=%d",
                     relayer_nonce, onchain_nonce, seq_nonce)
            tx_hash = None
            failed = 0
            for to, cd in calls:
                h = await _direct_exec(http, privy_wallet_id, eoa_address, safe_address, to, cd, nonce=str(seq_nonce))
                if h:
                    tx_hash = h
                    seq_nonce += 1
                else:
                    # tx reverted — Safe nonce did NOT increment, re-read on-chain to stay in sync
                    seq_nonce = int(await _get_nonce_onchain(http, safe_address))
                    failed += 1

            if failed == 0:
                result["ok"] = True
                result["tx_hash"] = tx_hash
                result["redeemed"] = len(calls)
                log.info("batch_neg_risk: OK redeemed=%d last_tx=%s", len(calls), tx_hash)
            elif tx_hash:
                result["ok"] = True  # partial success
                result["tx_hash"] = tx_hash
                result["redeemed"] = len(calls) - failed
                result["error"] = f"{failed} failed"
                log.warning("batch_neg_risk: partial redeemed=%d failed=%d", len(calls) - failed, failed)
            else:
                result["error"] = "all neg-risk redeems failed"

    except Exception as e:
        result["error"] = str(e)
        log.exception("batch_neg_risk: exception")

    return result


# ── Single redeem (binary only — neg-risk goes through redeem_all_neg_risk) ───

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
                # Delegate to batch function (single-item batch)
                batch_result = await redeem_all_neg_risk(
                    privy_wallet_id, eoa_address, safe_address,
                    [{"condition_id": condition_id, "token_id": known_token_id,
                      "size": estimated_tokens, "outcome": outcome}],
                )
                result["ok"]     = batch_result["ok"]
                result["tx_hash"] = batch_result["tx_hash"]
                result["error"]   = batch_result.get("error")

            else:
                # Binary — via relayer
                cur_nonce = nonce or await _get_safe_nonce(http, safe_address)
                mined = await _relayer_exec(
                    http, privy_wallet_id, eoa_address, safe_address,
                    CTF, _encode_binary_redeem(condition_id, PUSD), cur_nonce,
                )
                used_usdce = False
                if not mined:
                    log.info("redeem binary pUSD failed, trying USDC.e cond=%s", condition_id[:16])
                    cur_nonce = await _get_safe_nonce(http, safe_address)
                    mined = await _relayer_exec(
                        http, privy_wallet_id, eoa_address, safe_address,
                        CTF, _encode_binary_redeem(condition_id, USDC_E), cur_nonce,
                    )
                    used_usdce = bool(mined)

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
