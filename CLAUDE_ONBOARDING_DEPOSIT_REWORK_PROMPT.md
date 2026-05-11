# Mega prompt Claude Code - rework onboarding/deposit/fees Polymarket

Tu es Claude Code dans le repo `multi_user_bot`. Analyse le projet entier avant de modifier. Objectif: supprimer l'ancien parcours "wallet Arbitrum finance USDC + ETH pour fees + bridge automatique vers Polymarket" car les fees sont maintenant prises via le builder code Polymarket. L'app doit créer directement le compte Polymarket depuis le wallet Privy/Arbitrum, puis proposer des adresses de dépôt Polymarket multi-chain/token via l'API bridge Polymarket.

## Contexte architecture

- Backend FastAPI: `backend/app`.
- Frontend React/Vite: `frontend/src`.
- Rust service CLOB: `polymarket_rust_service`.
- DB principale: SQLAlchemy models dans `backend/app/db/models.py`; bootstrap idempotent dans `backend/app/db/bootstrap.py`; Alembic initial est placeholder.
- Login: `frontend/src/App.tsx` appelle `api.syncUser()`, puis backend `backend/app/api/auth.py` crée/récupère `User` + `Wallet`.
- Wallet Privy server wallet: `backend/app/services/privy_wallet_service.py`.
- Onboarding Polymarket actuel: `backend/app/services/polymarket_onboarding_service.py`.
- Bridge/funding actuel: `backend/app/api/user/wallets.py`, `backend/app/services/funding_service.py`, `backend/app/services/privy_tx_service.py`, `backend/app/integrations/polymarket/bridge_client.py`, `frontend/src/components/user/OnboardingModal.tsx`, `frontend/src/components/user/ProfileTab.tsx`, `frontend/src/components/user/BridgeHistoryModal.tsx`.
- Sessions/running guards actuels: `backend/app/runtime/session_manager.py`, `backend/app/runtime/session_engine.py`, `backend/app/services/balance_poller.py`, admin sessions/users frontend.
- Execution CLOB: `backend/app/integrations/polymarket/execution_client.py` ouvre un client Rust avec `privy_wallet_id`, EOA `wallet_address`, Safe `proxy_address`, signature type 2, builder code.
- Rust service supporte déjà `POLYMARKET_BUILDER_CODE`: `polymarket_rust_service/src/config.rs`, `live.rs`, `main.rs`, `models.rs`.

## Ce qui existe aujourd'hui et doit être compris

### Onboarding compte Polymarket

`backend/app/api/auth.py`:
- `POST /api/v1/auth/sync` crée/récupère un wallet Privy via `privy_wallet_service.get_or_create_wallet`.
- Si wallet `pending` ou `failed`, schedule `_run_onboarding_background`.
- `_run_onboarding_background` appelle `onboard_wallet`, stocke `proxy_address`, `clob_api_key`, `clob_api_secret`, `clob_api_passphrase`, passe `onboarding_state=ready`, puis tente de pré-cacher une adresse de dépôt bridge EVM dans `funding_deposit_address_cached`.
- Attention bug actuel: `onboarding_complete` semble surtout mis à true par `funding_status` quand un `trading_balance > 0`, pas forcément quand `onboarding_state=ready`.

`backend/app/services/polymarket_onboarding_service.py`:
- Dérive Safe depuis EOA, déploie Safe via relayer, crée/dérive creds CLOB, exécute approvals pUSD + CTF.
- Cela doit rester: on veut toujours un compte Polymarket/Safe/CLOB prêt à trader.
- Ne pas supprimer `proxy_address`, CLOB creds, approvals, V2 migration/reregister admin.

### Ancien funding à supprimer/remplacer

`backend/app/api/user/wallets.py`:
- Calcule USDC/ETH wallet Arbitrum via balance poller.
- `FundingStatusOut` expose `needs_usdc`, `needs_eth`, `min_usdc`, `min_eth`, `fee_wallet_*`, `bridge_min_usdc`, `bridgeable_usdc`, `funding_state`, `funding_tx_hash`, etc.
- `GET /funding-status` déclenche actuellement l'auto bridge si wallet funded, via `_ensure_cached_deposit_address` puis `transfer_usdc` depuis EOA vers deposit address.
- `POST /funding-force` force le même bridge.
- `GET /bridge-history` liste `FundingTransaction`.

`backend/app/services/funding_service.py`:
- Lit Arbitrum USDC/ETH, calcule reserve target, warn threshold, bridge amount, latest USDC tx.
- Garde utile: `get_trading_balance`, éventuellement balance pUSD/trading.
- À supprimer ou isoler: reserve Arbitrum, bridge amount, ETH/USDC fee wallet checks.

`backend/app/services/privy_tx_service.py` et `backend/app/services/fee_payment_service.py`:
- Envoient des USDC depuis le wallet Privy sur Arbitrum vers platform/referral.
- Avec builder fees, probablement ne plus payer les platform fees via Privy tx. À confirmer pour referral.

### Sessions/running

`backend/app/runtime/session_manager.py`:
- `launch_session` bloque si wallet Arbitrum USDC < `FEE_WALLET_USDC_STOP` ou ETH < `FEE_WALLET_ETH_MIN`.
- Il garde ensuite un check trading balance > 0 via `balance_poller.get_trading(user_id)`.
- À changer: retirer les guards Arbitrum fee wallet. Garder le guard trading balance Polymarket > 0 et le check `gross_capital <= trading balance` côté frontend/backend si nécessaire.

`backend/app/runtime/session_engine.py`:
- `_monitor_loop` appelle `_check_arbitrum_fee_buffer()` toutes les 5s/interval.
- `_check_arbitrum_fee_buffer` soft-stop si USDC/ETH Arbitrum trop bas.
- À supprimer/désactiver.
- `fee_service.process_lot_opened/closed` envoie encore des frais platform via queue Privy. Si builder code remplace les fees, il faut stopper cette charge locale ou la rendre no-op selon décision.
- `fee_reserve`, `paid_fees`, XP fees sont encore utilisés dans UI/analytics; décider si on garde comme métrique builder fee estimée ou si on met à zéro.

`backend/app/services/fee_service.py`:
- Calcule bps puis queue `fee_queue`.
- À désactiver si plus de tx fee wallet.
- Attention referral: aujourd'hui referral = 10% des fees payées, payé en USDC depuis EOA Arbitrum. Si fees builder Polymarket arrivent ailleurs, ce mécanisme ne marche plus tel quel.

### Frontend actuel à refondre

`frontend/src/components/user/OnboardingModal.tsx`:
- Flow actuel 4 steps: account setup, referral, username, funding.
- `Step3Funding` affiche "send USDC/ETH to Arbitrum wallet", reserve, bridge progress, `Bridge to Polymarket`, skip.
- À remplacer par un onboarding simplifié:
  1. account setup Polymarket direct depuis wallet Privy/Arbitrum;
  2. referral optionnel;
  3. username si nécessaire;
  4. deposit direct via adresse Polymarket avec select chain + token.
- Ne plus demander ETH/USDC sur Arbitrum pour fees.

`frontend/src/components/user/ProfileTab.tsx`:
- Deposit tab décrit Arbitrum wallet, ETH gas, reserve, auto bridge. À remplacer par dépôt direct Polymarket:
  - select chain: Arbitrum, Solana, Base;
  - select token selon chain:
    - Arbitrum: ETH, USDC
    - Solana: SOL, USDC
    - Base: ETH, USDC
  - afficher l'adresse de dépôt correspondante renvoyée/cachée par API Polymarket bridge;
  - afficher min deposit / minCheckoutUsd de Polymarket;
  - trading balance Polymarket + positions/redeem.
- `Bridge` button doit devenir `Deposit` ou `Funding`.
- `BridgeHistoryModal` doit devenir `History` ou `DepositHistory` si on garde status polling.

`frontend/src/pages/UserDashboard.tsx`:
- Top menu affiche Wallet USDC/ETH et banners `needs_usdc/needs_eth`. À supprimer/remplacer par seulement trading balance et éventuellement deposit status.
- Low balance banner doit pointer vers Profile/Deposit Polymarket.

`frontend/src/components/user/FaqTab.tsx`:
- Remplacer la FAQ bridge/réserve Arbitrum.

Admin:
- `frontend/src/pages/admin/Users.tsx` affiche colonnes Wallet USDC, ETH, POL; actions `Bridge POL`; warnings "no USDC/no ETH". À décider: supprimer ou garder comme debug seulement.
- `frontend/src/pages/admin/Sessions.tsx` launch admin marque no USDC/no ETH; à retirer.
- Backend admin `backend/app/api/admin/users.py` expose wallet balances Arbitrum et action bridge-pol; à décider si on garde bridge-pol uniquement comme tool legacy.

## Nouveau comportement demandé

### Création compte

- Créer/récupérer directement le compte Polymarket à partir du wallet Privy/Arbitrum existant.
- Garder Safe/proxy/CLOB/approvals.
- Quand `onboarding_state=ready`, considérer que le compte est prêt, indépendamment d'un dépôt.
- Ne plus conditionner `onboarding_complete` au bridge/trading balance. Introduire au besoin un champ distinct `account_setup_complete` ou réinterpréter `onboarding_complete = account setup complete`, mais attention aux usages existants.

### Dépôt

- Utiliser l'API Polymarket Bridge pour récupérer/créer des adresses de dépôt, une fois si absent en DB, sinon lire la DB.
- Ancien principe à garder: cache DB de l'adresse de dépôt, création une seule fois.
- Mais maintenant il faut gérer plusieurs address types:
  - EVM address pour Arbitrum/Base.
  - SVM address pour Solana.
- Chains/tokens supportés côté UI:
  - Arbitrum: ETH + USDC
  - Solana: SOL + USDC
  - Base: ETH + USDC
- Afficher min deposit Polymarket par chain/token (`minCheckoutUsd` depuis `/supported-assets` si disponible).
- Ne plus faire de transfert automatique depuis Privy wallet vers deposit address.
- Ne plus utiliser `BRIDGE_MIN_USDC`, reserve target, min bridge, wallet funded, ETH gas requirement.
- On peut garder un status polling `/status/{deposit_address}` pour history/progress, mais il ne doit pas piloter un auto-bridge.

### API backend proposée

Créer/remplacer endpoints, noms à décider selon style existant:

- `GET /api/v1/user/wallets/deposit-config`
  - Récupère primary wallet.
  - Nécessite `proxy_address`/Safe prêt.
  - Appelle `bridge_client.create_deposit_addresses(proxy_address)` si pas encore caché.
  - Stocke les adresses en DB. Actuellement une seule colonne `funding_deposit_address_cached`; prévoir migration/colonnes JSON ou colonnes séparées:
    - `deposit_address_evm`
    - `deposit_address_svm`
    - éventuellement `deposit_address_btc`, `deposit_address_tvm`
    - `deposit_addresses_cached_at`
  - Appelle/cache `bridge_client.get_supported_assets()`.
  - Filtre/normalise les assets pour Arbitrum/Base/Solana et ETH/USDC/SOL.
  - Retourne:
    - `polymarket_address` = `wallet.proxy_address`
    - `deposit_addresses`
    - `chains: [{ chain_id, chain_name, address_type, tokens: [{symbol,address,decimals,min_checkout_usd}] }]`
    - `trading_balance`, `positions_value`, `redeem_value`
    - `account_ready/onboarding_state`.

- `GET /api/v1/user/wallets/funding-status`
  - Peut rester pour compat frontend, mais doit devenir un simple status compte/deposit/trading balance.
  - Retourner les anciens champs avec valeurs neutres si nécessaire pendant migration frontend (`needs_usdc=false`, `needs_eth=false`, `bridge_available=false`) pour éviter casse.

- `GET /api/v1/user/wallets/deposit-status?address_type=evm|svm`
  - Optionnel: poll `bridge_client.get_transaction_status(deposit_address)`.

- `GET /api/v1/user/wallets/deposit-history`
  - Optionnel: remplacer `bridge-history`.

### DB/migrations

- Ne pas casser les DB existantes. Ajouter colonnes idempotentes dans `bootstrap.py`.
- Conserver les anciennes colonnes au début pour compat/backfill, mais ne plus s'en servir pour la logique critique.
- Si migration légère:
  - ajouter `deposit_address_evm`, `deposit_address_svm`, `deposit_address_btc`, `deposit_address_tvm`, `deposit_addresses_cached_at`.
  - backfill `deposit_address_evm = funding_deposit_address_cached` si présent.
- `FundingTransaction` peut devenir `DepositTransaction` plus tard, mais pour patch minimal, laisser ou renommer avec compat.

### Fees / builder code

- Le Rust service passe déjà `builder_code` à la config CLOB; vérifier que `POLYMARKET_BUILDER_CODE` est bien requis en env.
- Supprimer la dépendance produit aux wallets Arbitrum pour payer les fees.
- Stopper:
  - `session_manager.launch_session` USDC/ETH Arbitrum guards.
  - `session_engine._check_arbitrum_fee_buffer`.
  - auto-bridge funding.
  - frontend copy reserve/gas.
- Décision nécessaire: `fee_service` doit-il devenir no-op, ou doit-on continuer à compter des fees estimées sans les transférer?
- Si no-op complet: `paid_fees` peut rester 0, `fee_reserve_used` reste 0, referral rewards ne sont plus payées automatiquement. Mais cela casse XP/referral/analytics fee displays.
- Alternative: garder `compute_fill_fee` pour analytics, mais ne pas appeler `pay_fee`/Privy tx. Enregistrer "fees attributed by builder" séparément si possible.

## Fichiers à inspecter/modifier

Backend:
- `backend/app/api/auth.py`: account setup complete, deposit address precache changes.
- `backend/app/api/user/wallets.py`: remplacer funding/bridge automatique par deposit config/status.
- `backend/app/integrations/polymarket/bridge_client.py`: ajouter normalisation si utile.
- `backend/app/services/funding_service.py`: enlever ou isoler Arbitrum reserve helpers.
- `backend/app/services/balance_poller.py`: peut ne plus poller wallet_usdc/eth pour user UI; garder trading/positions. Admin peut garder debug si voulu.
- `backend/app/runtime/session_manager.py`: retirer launch guard Arbitrum USDC/ETH, garder trading balance guard.
- `backend/app/runtime/session_engine.py`: retirer `_check_arbitrum_fee_buffer`; revoir fee_service calls.
- `backend/app/services/fee_service.py`, `fee_payment_service.py`, `privy_tx_service.py`: désactiver platform fee Privy payments si builder fees.
- `backend/app/db/models.py`, `backend/app/db/bootstrap.py`: nouvelles colonnes deposit addresses et compat.
- `backend/app/api/admin/users.py`: admin balances/actions/copy legacy.
- `backend/app/api/admin/sessions.py`: admin launch uses session_manager, no special change sauf copy frontend.
- `backend/app/core/config.py`: enlever/mettre legacy les vars `FUNDING_*`, `FEE_WALLET_*`, `BRIDGE_MIN_USDC`, ou les garder non utilisées; documenter `POLYMARKET_BUILDER_CODE`.

Frontend:
- `frontend/src/components/user/OnboardingModal.tsx`: refondre Step funding en deposit selector.
- `frontend/src/components/user/ProfileTab.tsx`: refondre Deposit tab.
- `frontend/src/components/user/BridgeHistoryModal.tsx`: renommer/history deposit.
- `frontend/src/pages/UserDashboard.tsx`: enlever wallet USDC/ETH banners et menu items.
- `frontend/src/utils/api.ts`: nouveaux endpoints.
- `frontend/src/types/index.ts`: nouveaux types deposit config/chains/tokens, retirer/legacy bridge types.
- `frontend/src/utils/bridge.ts`, `depositMonitor.ts`: adapter au vrai deposit status si encore utile.
- `frontend/src/components/user/FaqTab.tsx`: update copy.
- `frontend/src/pages/admin/Users.tsx`, `frontend/src/pages/admin/Sessions.tsx`: retirer warnings USDC/ETH gas et bridge POL si obsolète.
- `frontend/src/pages/admin/Strategies.tsx`: copy `reserve_pct` "USDC buffer for fees" à revoir si reserve_pct n'est plus fee reserve.

Rust:
- Ne modifier que si nécessaire.
- Vérifier que `builder_code` reste passé dans `Config::builder().builder_code`.
- Ne pas casser `proxy_address` as funder/Safe.

## Tests/validation attendus

- Backend démarre.
- Frontend build.
- Login new user:
  - wallet Privy créé/récupéré;
  - Safe/proxy/CLOB prêt;
  - onboarding prêt sans demander ETH/USDC Arbitrum.
- Deposit UI:
  - affiche Arbitrum/Base/Solana;
  - affiche tokens demandés;
  - affiche adresse EVM ou SVM correcte;
  - affiche min deposit issu de Polymarket bridge assets;
  - ne lance aucun transfert automatique.
- Existing user:
  - anciennes colonnes n'empêchent pas le chargement;
  - si `funding_deposit_address_cached` existait, backfill EVM possible.
- Session launch:
  - ne bloque plus sur wallet USDC/ETH;
  - bloque encore si trading balance Polymarket insuffisante/zero;
  - running session ne soft-stop plus à cause d'USDC/ETH Arbitrum.
- Fee system:
  - selon décision, aucune tx Privy fee n'est envoyée, ou seulement analytics est conservé.

## Questions à trancher avant codage

1. Est-ce que `onboarding_complete` doit maintenant signifier "compte Polymarket prêt" plutôt que "dépôt/trading balance prêt"?
2. Est-ce que le dépôt doit rester une étape obligatoire dans onboarding, ou être skippable comme aujourd'hui?
3. Quand le compte est prêt mais trading balance = 0, l'utilisateur doit-il pouvoir accéder à toute l'app sauf launch session?
4. Pour les fees builder code: doit-on désactiver totalement `fee_service` et `fee_queue`, ou garder une estimation analytics sans paiement on-chain?
5. Que deviennent les referrals "10% des fees en USDC vers wallet Arbitrum" si les fees sont prises par Polymarket builder code?
6. Si referral reste, d'où vient le paiement: wallet platform, revenue builder off-chain, ou plus tard manuel?
7. Faut-il enlever la campagne referral de l'UI tant que payout builder-code n'est pas implémenté?
8. `reserve_pct` de stratégie sert actuellement à retenir un fee reserve dans le capital de session. Est-ce encore utile? Si oui, c'est une réserve risque/SL, plus une réserve fee.
9. Le capital lancé doit-il être limité au `trading_balance` disponible moins positions/redeem?
10. Pour dépôt ETH/SOL via bridge, Polymarket reçoit-il forcément pUSD/trading balance? Vérifier via supported-assets/minCheckoutUsd.
11. Faut-il afficher les adresses de dépôt générées par Polymarket pour toutes chains ou seulement l'adresse correspondant au selector?
12. Pour Arbitrum et Base, même adresse EVM? Probablement oui via `address.evm`; confirmer.
13. Pour Solana, utiliser `address.svm`; confirmer que l'API `/deposit` retourne bien SVM pour le Safe.
14. Faut-il stocker supported assets en DB/cache ou les récupérer à chaque demande?
15. L'historique doit-il être basé sur `/status/{deposit_address}` ou garder seulement l'ancien `funding_transactions` local?
16. Si l'utilisateur dépose sur Base/Solana, comment afficher l'explorer tx dans history?
17. L'ancien `funding-force` doit-il être supprimé, gardé en 410 Gone, ou gardé no-op compat?
18. Les colonnes admin Wallet USDC/ETH/POL doivent-elles disparaître ou rester en debug admin?
19. L'action admin `Bridge POL` doit-elle rester?
20. Les env vars `FEE_WALLET_*`, `FUNDING_*`, `BRIDGE_MIN_USDC`, `ARBITRUM_*` doivent-elles être retirées de `.env.production` ou laissées legacy?
21. La modale withdraw doit-elle changer maintenant que l'utilisateur ne finance plus via wallet Arbitrum?
22. Faut-il demander à l'utilisateur d'export wallet pour withdraw, ou ajouter une future feature withdrawal intégrée?
23. Doit-on garder l'auto free-fee period/new_user_free_fee_days, ou c'est obsolète avec builder fees?
24. Les XP tiers basés sur fee bps doivent-ils rester visibles?
25. Si fees builder code sont automatiques côté Polymarket, quelle valeur de fee bps afficher à l'utilisateur?
26. Les tests doivent-ils privilégier compat minimale ou grand nettoyage des anciens noms "bridge/funding"?

## Contraintes

- Ne pas casser la création Safe/CLOB/approvals.
- Ne pas supprimer des colonnes existantes sans migration sûre.
- Ne pas casser les sessions running: une session active doit continuer même si UI/API funding change.
- Ne pas envoyer de transaction USDC automatique depuis Privy wallet pour bridge ou fees dans le nouveau flow, sauf décision explicite.
- Garder le style UI existant, sobre, dense, pas de landing page.
- Après changement, exécuter au minimum:
  - backend import/syntax check;
  - frontend `npm run build`;
  - si Rust modifié, `cargo build`.
