from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import httpx
from eth_abi import encode as abi_encode
from eth_utils import keccak, to_checksum_address
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Session as DBSession, Wallet
from app.services.polymarket_onboarding_service import (
    CTF,
    PUSD,
    SAFE_MULTISEND,
    USDC,
    ZERO_ADDRESS,
    _encode_multisend,
    _get_safe_nonce,
    _poll_transaction,
    _relayer_post_authed,
    _sign_safe_tx,
)
from app.services.privy_tx_service import PrivyTxError, _privy_rpc

log = logging.getLogger("uvicorn.error")

_ZERO_BYTES32 = b"\x00" * 32
_REDEEM_SELECTOR = keccak(text="redeemPositions(address,bytes32,bytes32,uint256[])")[:4]


class RedeemService:
    async def redeem_session_winnings(
        self,
        db: AsyncSession,
        session_id: str,
        *,
        trigger: str,
    ) -> dict[str, Any]:
        log.info("[redeem] session=%s trigger=%s start", session_id[:8], trigger)
        session = await db.get(DBSession, session_id)
        if not session:
            log.warning("[redeem] session=%s trigger=%s session not found", session_id[:8], trigger)
            return {"ok": False, "reason": "session_not_found"}

        wallet = await db.get(Wallet, session.wallet_id)
        if not wallet or not wallet.privy_wallet_id or not wallet.wallet_address:
            log.warning(
                "[redeem] session=%s trigger=%s wallet not ready wallet_id=%s has_privy=%s has_wallet=%s",
                session_id[:8],
                trigger,
                getattr(session, "wallet_id", None),
                bool(wallet and wallet.privy_wallet_id),
                bool(wallet and wallet.wallet_address),
            )
            return {"ok": False, "reason": "wallet_not_ready"}

        holder = wallet.proxy_address or wallet.wallet_address
        log.info(
            "[redeem] session=%s trigger=%s holder=%s wallet=%s safe=%s",
            session_id[:8],
            trigger,
            holder[:10],
            wallet.wallet_address[:10],
            (wallet.proxy_address or "-")[:10],
        )
        positions = await self._fetch_redeemable_positions(holder)
        if not positions:
            log.info(
                "[redeem] session=%s trigger=%s holder=%s no redeemable positions",
                session_id[:8], trigger, holder[:10],
            )
            return {"ok": True, "redeemed": 0, "reason": "nothing_redeemable"}

        log.info(
            "[redeem] session=%s trigger=%s holder=%s fetched_positions=%d sample=%s",
            session_id[:8],
            trigger,
            holder[:10],
            len(positions),
            self._positions_preview(positions),
        )
        attempts = await self._build_redeem_attempts(positions)
        if not attempts:
            log.warning(
                "[redeem] session=%s trigger=%s holder=%s positions=%d but no valid redeem calls",
                session_id[:8],
                trigger,
                holder[:10],
                len(positions),
            )
            return {"ok": True, "redeemed": 0, "reason": "no_valid_calls"}
        last_result: dict[str, Any] | None = None
        for attempt_idx, attempt in enumerate(attempts, start=1):
            txs = attempt["txs"]
            collateral_labels = attempt["collateral_labels"]
            log.info(
                "[redeem] session=%s trigger=%s attempt=%d/%d collaterals=%s calls=%d",
                session_id[:8],
                trigger,
                attempt_idx,
                len(attempts),
                collateral_labels,
                len(txs),
            )
            try:
                if wallet.proxy_address:
                    log.info(
                        "[redeem] session=%s trigger=%s submitting SAFE redeems calls=%d safe=%s",
                        session_id[:8],
                        trigger,
                        len(txs),
                        wallet.proxy_address[:10],
                    )
                    result = await self._submit_safe_redeems(
                        privy_wallet_id=wallet.privy_wallet_id,
                        eoa_address=wallet.wallet_address,
                        safe_address=wallet.proxy_address,
                        txs=txs,
                        trigger=trigger,
                    )
                else:
                    log.info(
                        "[redeem] session=%s trigger=%s submitting EOA redeems calls=%d wallet=%s",
                        session_id[:8],
                        trigger,
                        len(txs),
                        wallet.wallet_address[:10],
                    )
                    result = await self._submit_eoa_redeems(
                        privy_wallet_id=wallet.privy_wallet_id,
                        wallet_address=wallet.wallet_address,
                        txs=txs,
                    )
            except Exception as e:
                log.warning(
                    "[redeem] session=%s trigger=%s attempt=%d failed: %s",
                    session_id[:8],
                    trigger,
                    attempt_idx,
                    e,
                )
                last_result = {"ok": False, "reason": str(e), "redeemed": len(txs)}
                continue

            verified = await self._verify_redeem_consumed(
                holder=holder,
                initial_positions=positions,
            )
            if verified["verified"]:
                log.info(
                    "[redeem] session=%s trigger=%s holder=%s redeem_calls=%d result=%s",
                    session_id[:8], trigger, holder[:10], len(txs), result,
                )
                return {
                    "ok": True,
                    "redeemed": len(txs),
                    "attempt": attempt_idx,
                    "collaterals": collateral_labels,
                    **result,
                    **verified,
                }

            log.warning(
                "[redeem] session=%s trigger=%s holder=%s attempt=%d tx submitted but positions still redeemable remaining=%s",
                session_id[:8],
                trigger,
                holder[:10],
                attempt_idx,
                verified["remaining_preview"],
            )
            last_result = {
                "ok": False,
                "redeemed": len(txs),
                "reason": "redeem_not_consumed",
                "attempt": attempt_idx,
                "collaterals": collateral_labels,
                **result,
                **verified,
            }

        return last_result or {"ok": False, "reason": "redeem_not_attempted", "redeemed": 0}

    async def _fetch_redeemable_positions(self, holder: str) -> list[dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=15.0, trust_env=False) as http:
                resp = await http.get(
                    f"{settings.DATA_API_HOST}/positions",
                    params={
                        "user": holder,
                        "redeemable": "true",
                        "sizeThreshold": "0.01",
                        "limit": "500",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                count = len(data) if isinstance(data, list) else 0
                log.info(
                    "[redeem] fetch positions holder=%s status=%s count=%d",
                    holder[:10],
                    resp.status_code,
                    count,
                )
                return data if isinstance(data, list) else []
        except Exception as e:
            log.warning("[redeem] fetch positions failed for %s: %s", holder[:10], e)
            return []

    async def _build_redeem_attempts(self, positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"index_sets": set(), "positions": []})
        skipped = 0
        for pos in positions:
            condition_id = self._normalize_bytes32(pos.get("conditionId") or pos.get("condition_id"))
            outcome_index = pos.get("outcomeIndex", pos.get("outcome_index"))
            if not condition_id or outcome_index is None:
                skipped += 1
                continue
            try:
                grouped[condition_id]["index_sets"].add(1 << int(outcome_index))
                grouped[condition_id]["positions"].append(pos)
            except Exception:
                skipped += 1
                continue

        plans: list[dict[str, Any]] = []
        for condition_id, payload in grouped.items():
            market = await self._fetch_market_metadata(condition_id)
            index_sets = await self._resolve_index_sets(
                condition_id=condition_id,
                market=market,
                position_index_sets=sorted(payload["index_sets"]),
            )
            plans.append({
                "condition_id": condition_id,
                "index_sets": index_sets,
                "collaterals": self._resolve_collateral_candidates(market),
            })

        attempts: list[dict[str, Any]] = []
        attempt_count = max((len(plan["collaterals"]) for plan in plans), default=0)
        for attempt_idx in range(attempt_count):
            txs: list[dict[str, str]] = []
            labels: list[str] = []
            for plan in plans:
                collateral = plan["collaterals"][min(attempt_idx, len(plan["collaterals"]) - 1)]
                calldata = self._encode_redeem_positions(plan["condition_id"], plan["index_sets"], collateral)
                txs.append({"to": CTF, "data": calldata, "value": "0", "operation": 0})
                labels.append(self._collateral_label(collateral))
            attempts.append({"txs": txs, "collateral_labels": labels})
        log.info(
            "[redeem] build txs positions=%d conditions=%d attempts=%d skipped=%d",
            len(positions),
            len(grouped),
            len(attempts),
            skipped,
        )
        return attempts

    def _positions_preview(self, positions: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
        preview: list[dict[str, Any]] = []
        for pos in positions[:limit]:
            preview.append({
                "condition": (pos.get("conditionId") or pos.get("condition_id") or "")[:10],
                "outcome": pos.get("outcomeIndex", pos.get("outcome_index")),
                "size": pos.get("size"),
                "redeemable": pos.get("redeemable"),
            })
        return preview

    async def _resolve_index_sets(
        self,
        *,
        condition_id: str,
        market: dict[str, Any] | None,
        position_index_sets: list[int],
    ) -> list[int]:
        outcome_count = self._extract_outcome_count(market)
        if outcome_count and outcome_count >= 2:
            index_sets = [1 << i for i in range(outcome_count)]
            log.info(
                "[redeem] condition=%s using full partition outcome_count=%d index_sets=%s",
                condition_id[:10],
                outcome_count,
                index_sets,
            )
            return index_sets

        if len(position_index_sets) == 1 and position_index_sets[0] in (1, 2):
            log.info(
                "[redeem] condition=%s fallback binary partition index_sets=%s",
                condition_id[:10],
                [1, 2],
            )
            return [1, 2]

        log.info(
            "[redeem] condition=%s fallback position index_sets=%s",
            condition_id[:10],
            position_index_sets,
        )
        return position_index_sets

    def _extract_outcome_count(self, market: dict[str, Any] | None) -> int | None:
        if not isinstance(market, dict):
            return None
        tokens = market.get("tokens")
        if isinstance(tokens, list) and len(tokens) >= 2:
            return len(tokens)
        outcomes = market.get("outcomes")
        if isinstance(outcomes, str):
            try:
                import json
                parsed = json.loads(outcomes)
                if isinstance(parsed, list) and len(parsed) >= 2:
                    return len(parsed)
            except Exception:
                pass
        if isinstance(outcomes, list) and len(outcomes) >= 2:
            return len(outcomes)
        return None

    async def _fetch_market_metadata(self, condition_id: str) -> dict[str, Any] | None:
        normalized = "0x" + condition_id.removeprefix("0x")
        try:
            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as http:
                resp = await http.get(
                    f"{settings.GAMMA_HOST}/markets",
                    params={"conditionId": normalized},
                )
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and str(item.get("conditionId") or "").lower() == normalized.lower():
                            return item
                    return data[0] if data and isinstance(data[0], dict) else None
                if isinstance(data, dict):
                    markets = data.get("markets")
                    if isinstance(markets, list):
                        for item in markets:
                            if isinstance(item, dict) and str(item.get("conditionId") or "").lower() == normalized.lower():
                                return item
                        return markets[0] if markets and isinstance(markets[0], dict) else None
                    return data
        except Exception as e:
            log.warning("[redeem] fetch market metadata failed for %s: %s", condition_id[:10], e)
        return None

    def _resolve_collateral_candidates(self, market: dict[str, Any] | None) -> list[str]:
        candidates: list[str] = []
        if isinstance(market, dict):
            for raw in (market.get("denominationToken"), market.get("collateralToken")):
                if not raw:
                    continue
                token = str(raw).strip()
                lowered = token.lower()
                if lowered in {USDC.lower(), PUSD.lower()}:
                    candidates.append(self._checksum_or_raw(token))
                elif "pusd" in lowered:
                    candidates.append(PUSD)
                elif "usdc" in lowered:
                    candidates.append(USDC)

        ordered = candidates + [USDC, PUSD]
        unique: list[str] = []
        seen: set[str] = set()
        for token in ordered:
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(token)
        return unique

    def _collateral_label(self, collateral_token: str) -> str:
        lowered = collateral_token.lower()
        if lowered == USDC.lower():
            return "USDC"
        if lowered == PUSD.lower():
            return "PUSD"
        return collateral_token[:10]

    def _checksum_or_raw(self, value: str) -> str:
        try:
            return to_checksum_address(value)
        except Exception:
            return value

    def _position_keys(self, positions: list[dict[str, Any]]) -> set[tuple[str, int]]:
        keys: set[tuple[str, int]] = set()
        for pos in positions:
            condition_id = self._normalize_bytes32(pos.get("conditionId") or pos.get("condition_id"))
            outcome_index = pos.get("outcomeIndex", pos.get("outcome_index"))
            if not condition_id or outcome_index is None:
                continue
            try:
                keys.add((condition_id, int(outcome_index)))
            except Exception:
                continue
        return keys

    async def _verify_redeem_consumed(
        self,
        *,
        holder: str,
        initial_positions: list[dict[str, Any]],
        polls: int = 4,
        delay_seconds: float = 2.0,
    ) -> dict[str, Any]:
        expected_keys = self._position_keys(initial_positions)
        remaining: list[dict[str, Any]] = []
        for attempt in range(1, polls + 1):
            if attempt > 1:
                await self._sleep(delay_seconds)
            remaining = await self._fetch_redeemable_positions(holder)
            remaining_keys = self._position_keys(remaining)
            unresolved = expected_keys & remaining_keys
            log.info(
                "[redeem] verify holder=%s attempt=%d/%d initial=%d remaining=%d unresolved=%d",
                holder[:10],
                attempt,
                polls,
                len(expected_keys),
                len(remaining_keys),
                len(unresolved),
            )
            if not unresolved:
                return {
                    "verified": True,
                    "remaining_redeemable": len(remaining_keys),
                    "remaining_preview": self._positions_preview(remaining),
                }

        remaining_keys = self._position_keys(remaining)
        unresolved = expected_keys & remaining_keys
        return {
            "verified": False,
            "remaining_redeemable": len(remaining_keys),
            "remaining_preview": self._positions_preview(remaining),
            "remaining_expected": [
                {"condition": condition_id[:10], "outcome": outcome}
                for condition_id, outcome in sorted(unresolved)[:3]
            ],
        }

    def _encode_redeem_positions(self, condition_id: str, index_sets: list[int], collateral_token: str) -> str:
        encoded = abi_encode(
            ["address", "bytes32", "bytes32", "uint256[]"],
            [
                to_checksum_address(collateral_token),
                _ZERO_BYTES32,
                bytes.fromhex(condition_id),
                index_sets,
            ],
        )
        return "0x" + (_REDEEM_SELECTOR + encoded).hex()

    def _normalize_bytes32(self, value: Any) -> str | None:
        if not value:
            return None
        raw = str(value).lower().removeprefix("0x")
        if len(raw) > 64:
            return None
        return raw.rjust(64, "0")

    async def _submit_safe_redeems(
        self,
        *,
        privy_wallet_id: str,
        eoa_address: str,
        safe_address: str,
        txs: list[dict[str, str]],
        trigger: str,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as http:
            if len(txs) == 1:
                agg = txs[0]
                operation = agg["operation"]
            else:
                agg = {
                    "to": SAFE_MULTISEND,
                    "data": _encode_multisend(txs),
                    "value": "0",
                    "operation": 1,
                }
                operation = 1

            nonce = await _get_safe_nonce(http, safe_address)
            log.info(
                "[redeem] SAFE prepare safe=%s eoa=%s calls=%d operation=%s nonce=%s",
                safe_address[:10],
                eoa_address[:10],
                len(txs),
                operation,
                nonce,
            )
            sig = await _sign_safe_tx(
                privy_wallet_id,
                safe_address,
                agg["to"],
                agg["data"],
                agg["value"],
                operation,
                nonce,
            )
            body = {
                "from": eoa_address,
                "to": agg["to"],
                "proxyWallet": safe_address,
                "data": agg["data"],
                "nonce": nonce,
                "signature": sig,
                "signatureParams": {
                    "gasPrice": "0",
                    "operation": str(operation),
                    "safeTxnGas": "0",
                    "baseGas": "0",
                    "gasToken": ZERO_ADDRESS,
                    "refundReceiver": ZERO_ADDRESS,
                },
                "type": "SAFE",
                "metadata": f"redeem winnings after {trigger}",
            }
            submit = await _relayer_post_authed(http, "/submit", body)
            tx_id = submit.get("transactionID")
            log.info(
                "[redeem] SAFE submitted safe=%s tx_id=%s response=%s",
                safe_address[:10],
                tx_id,
                submit,
            )
            receipt = await _poll_transaction(http, tx_id, max_polls=30) if tx_id else None
            if tx_id and not receipt:
                log.warning(
                    "[redeem] SAFE tx_id=%s no receipt after polling",
                    tx_id,
                )
                raise RuntimeError(f"safe redeem transaction failed or timed out: {tx_id}")
            log.info(
                "[redeem] SAFE confirmed safe=%s tx_id=%s receipt=%s",
                safe_address[:10],
                tx_id,
                receipt,
            )
            return {
                "relay_transaction_id": tx_id,
                "relay_transaction_hash": receipt.get("transactionHash") if isinstance(receipt, dict) else None,
                "relay_state": receipt.get("state") if isinstance(receipt, dict) else None,
            }

    async def _submit_eoa_redeems(
        self,
        *,
        privy_wallet_id: str,
        wallet_address: str,
        txs: list[dict[str, str]],
    ) -> dict[str, Any]:
        hashes: list[str] = []
        for idx, tx in enumerate(txs, start=1):
            payload = {
                "method": "eth_sendTransaction",
                "caip2": f"eip155:{settings.CHAIN_ID}",
                "params": {
                    "transaction": {
                        "from": wallet_address,
                        "to": tx["to"],
                        "data": tx["data"],
                        "value": "0x0",
                    }
                },
            }
            try:
                log.info(
                    "[redeem] EOA send wallet=%s tx=%d/%d to=%s",
                    wallet_address[:10],
                    idx,
                    len(txs),
                    tx["to"][:10],
                )
                result = await _privy_rpc(privy_wallet_id, payload)
            except PrivyTxError as e:
                raise RuntimeError(f"EOA redeem failed: {e}") from e
            data = result.get("data") or {}
            tx_hash = (
                data.get("hash")
                or data.get("txHash")
                or data.get("tx_hash")
                or result.get("result")
                or result.get("hash")
            )
            if tx_hash:
                hashes.append(tx_hash)
            log.info(
                "[redeem] EOA response wallet=%s tx=%d/%d hash=%s raw=%s",
                wallet_address[:10],
                idx,
                len(txs),
                tx_hash,
                result,
            )
        return {"tx_hashes": hashes}

    async def _sleep(self, seconds: float) -> None:
        import asyncio
        await asyncio.sleep(seconds)


redeem_service = RedeemService()
