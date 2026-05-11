# Mega prompt Claude Code - onboarding/deposit/fees Polymarket rework

Tu es Claude Code dans le repo `multi_user_bot`. Analyse le projet entier avant de modifier. Objectif: supprimer l'ancien parcours "wallet Arbitrum finance USDC + ETH pour fees + bridge automatique vers Polymarket". Il n'y a plus aucune fee applicative a prelever/payer/envoyer. L'app doit creer directement le compte Polymarket depuis le wallet Privy/Arbitrum, puis proposer des adresses de depot Polymarket multi-chain/token via l'API bridge Polymarket. Le depot reel est fait par l'utilisateur lui-meme vers une adresse de depot Polymarket, pas par le wallet Privy et pas par un auto-bridge backend.

## Architecture a comprendre

- Backend FastAPI: `backend/app`.
- Frontend React/Vite: `frontend/src`.
- Rust service CLOB: `polymarket_rust_service`.
- DB: SQLAlchemy models dans `backend/app/db/models.py`; bootstrap idempotent dans `backend/app/db/bootstrap.py`; Alembic initial est placeholder.
- Login: `frontend/src/App.tsx` appelle `api.syncUser()`, puis `backend/app/api/auth.py` cree/recupere `User` + `Wallet`.
- Wallet Privy server wallet: `backend/app/services/privy_wallet_service.py`.
- Onboarding Polymarket actuel: `backend/app/services/polymarket_onboarding_service.py`.
- Bridge/funding actuel: `backend/app/api/user/wallets.py`, `backend/app/services/funding_service.py`, `backend/app/services/privy_tx_service.py`, `backend/app/integrations/polymarket/bridge_client.py`, `frontend/src/components/user/OnboardingModal.tsx`, `frontend/src/components/user/ProfileTab.tsx`, `frontend/src/components/user/BridgeHistoryModal.tsx`.
- Sessions/running guards: `backend/app/runtime/session_manager.py`, `backend/app/runtime/session_engine.py`, `backend/app/services/balance_poller.py`, admin sessions/users frontend.
- Execution CLOB: `backend/app/integrations/polymarket/execution_client.py` ouvre un client Rust avec `privy_wallet_id`, EOA `wallet_address`, Safe `proxy_address`, signature type 2, builder code.
- Rust service supporte deja `POLYMARKET_BUILDER_CODE`: `polymarket_rust_service/src/config.rs`, `live.rs`, `main.rs`, `models.rs`.

## Decisions produit tranchees

- Le compte technique Polymarket est pret quand `onboarding_state=ready`: Safe/proxy/CLOB/approvals sont OK.
- `onboarding_complete` ne veut PAS dire "compte technique pret"; il doit rester lie au vrai depot utilisateur. Il devient `true` quand `trading_balance > 0` a ete observe au moins une fois, ce qui prouve que l'utilisateur a deja depose/credite son compte Polymarket.
- Ne pas repasser `onboarding_complete=false` si l'utilisateur retire ensuite ses fonds. Ajouter/exposer un etat live separe, par exemple `is_funded_now = trading_balance > 0` et/ou `trading_cash_available`.
- L'utilisateur peut skip le depot pendant l'onboarding et revenir plus tard.
- L'utilisateur doit pouvoir entrer dans l'app sans depot, mais il ne peut pas lancer une session tant que le trading balance Polymarket est a 0 ou insuffisant pour le capital demande.
- Les sessions ne doivent plus jamais bloquer ni s'arreter a cause du wallet Arbitrum USDC/ETH.
- Les seules raisons normales d'arret session: session SL, target volume atteint, duree atteinte, stop manuel/admin, erreur trading/recovery.
- Plus aucune fee applicative: desactiver prelevement, fee queue, paiements platform, paiements referral, warnings/reserves fee wallet.
- Garder le principe referral/UI/DB si souhaite, mais aucune action financiere ne se passe derriere pour l'instant. Ne plus promettre un payout automatique actif. Les builder fees Polymarket ne permettent pas de referral/payout cote app.
- Tout ce qui est bridge history local, auto-bridge, poll de bridge, min bridge, reserve USDC/ETH, fix funding, funding force ne sert plus et doit etre supprime ou rendu legacy/no-op proprement.
- Transition obligatoire: ne rien perdre pour les users actuels, wallets actuels, profiles actuels, sessions actuelles, bridge/funding history actuelle et tables DB existantes. Garder les tables/colonnes legacy en BDD, mais ne plus les utiliser comme logique produit.

## Ce qui existe aujourd'hui

### Onboarding compte Polymarket

`backend/app/api/auth.py`:
- `POST /api/v1/auth/sync` cree/recupere un wallet Privy via `privy_wallet_service.get_or_create_wallet`.
- Si wallet `pending` ou `failed`, schedule `_run_onboarding_background`.
- `_run_onboarding_background` appelle `onboard_wallet`, stocke `proxy_address`, `clob_api_key`, `clob_api_secret`, `clob_api_passphrase`, passe `onboarding_state=ready`, puis tente de precacher une adresse de depot bridge EVM dans `funding_deposit_address_cached`.
- Actuellement `onboarding_complete` est surtout mis a true par `funding_status` quand `trading_balance > 0`. Garder cette idee, mais separer clairement `account_ready` de `onboarding_complete`.

`backend/app/services/polymarket_onboarding_service.py`:
- Derive Safe depuis EOA, deploie Safe via relayer, cree/derive creds CLOB, execute approvals pUSD + CTF.
- Cela doit rester: on veut toujours un compte Polymarket/Safe/CLOB pret a trader.
- Ne pas supprimer `proxy_address`, CLOB creds, approvals, V2 migration/reregister admin.

### Ancien funding a supprimer/remplacer

`backend/app/api/user/wallets.py`:
- Calcule USDC/ETH wallet Arbitrum via balance poller.
- `FundingStatusOut` expose `needs_usdc`, `needs_eth`, `min_usdc`, `min_eth`, `fee_wallet_*`, `bridge_min_usdc`, `bridgeable_usdc`, `funding_state`, `funding_tx_hash`, etc.
- `GET /funding-status` declenche actuellement l'auto bridge si wallet funded, via `_ensure_cached_deposit_address` puis `transfer_usdc` depuis EOA vers deposit address.
- `POST /funding-force` force le meme bridge.
- `GET /bridge-history` liste `FundingTransaction`.

`backend/app/services/funding_service.py`:
- Lit Arbitrum USDC/ETH, calcule reserve target, warn threshold, bridge amount, latest USDC tx.
- Garder utile si besoin: `get_trading_balance`, positions/trading balance helpers.
- A supprimer/isoler: reserve Arbitrum, bridge amount, ETH/USDC fee wallet checks.

`backend/app/services/privy_tx_service.py`, `fee_payment_service.py`, `fee_service.py`:
- Envoient/queue des USDC depuis le wallet Privy sur Arbitrum vers platform/referral.
- Nouveau modele: plus aucun paiement fee/referral via Privy tx. `fee_service` devient no-op cote prelevement/paiement.

### Sessions/running

`backend/app/runtime/session_manager.py`:
- `launch_session` bloque si wallet Arbitrum USDC < `FEE_WALLET_USDC_STOP` ou ETH < `FEE_WALLET_ETH_MIN`.
- A changer: retirer ces guards Arbitrum fee wallet.
- Garder le guard trading balance Polymarket > 0 et ajouter/garantir `gross_capital <= trading_balance disponible` si necessaire.

`backend/app/runtime/session_engine.py`:
- `_monitor_loop` appelle `_check_arbitrum_fee_buffer()`.
- `_check_arbitrum_fee_buffer` soft-stop si USDC/ETH Arbitrum trop bas.
- A supprimer/desactiver.
- `fee_service.process_lot_opened/closed` charge encore des fees. A desactiver: aucun `pay_fee`, aucun `transfer_usdc`, aucune `fee_queue` active.
- `paid_fees` peut rester a 0; adapter l'UI/analytics pour ne plus presenter ces valeurs comme frais factures par l'app.

## Nouveau depot utilisateur

- Utiliser l'API Polymarket Bridge pour recuperer/creer les adresses de depot, une fois si absent en DB, sinon lire la DB.
- Ancien principe a garder: cache DB de l'adresse de depot, creation une seule fois.
- Maintenant gerer plusieurs address types:
  - EVM address pour Arbitrum/Base.
  - SVM address pour Solana.
- Chains/tokens supportes cote UI:
  - Arbitrum: ETH + USDC
  - Solana: SOL + USDC
  - Base: ETH + USDC
- Afficher le min deposit Polymarket par chain/token (`minCheckoutUsd` depuis `/supported-assets` si disponible).
- Ne plus faire de transfert automatique depuis Privy wallet vers deposit address.
- Ne plus utiliser `BRIDGE_MIN_USDC`, reserve target, min bridge, wallet funded, ETH gas requirement.
- Ne pas garder de poll/history bridge inutile. Supprimer `BridgeHistoryModal` ou le remplacer uniquement par une UI utile si l'API Polymarket apporte une valeur claire. Par defaut: pas de polling de bridge.

## API backend souhaitee

Creer/remplacer endpoints selon le style existant:

- `GET /api/v1/user/wallets/deposit-config`
  - Recupere primary wallet.
  - Necessite `proxy_address`/Safe pret.
  - Appelle `bridge_client.create_deposit_addresses(proxy_address)` si pas encore cache.
  - Stocke les adresses en DB. Ajouter colonnes ou JSON:
    - `deposit_address_evm`
    - `deposit_address_svm`
    - eventuellement `deposit_address_btc`, `deposit_address_tvm`
    - `deposit_addresses_cached_at`
  - Backfill `deposit_address_evm = funding_deposit_address_cached` si present.
  - Appelle/cache `bridge_client.get_supported_assets()`.
  - Filtre/normalise les assets pour Arbitrum/Base/Solana et ETH/USDC/SOL.
  - Retourne:
    - `polymarket_address` = `wallet.proxy_address`
    - `deposit_addresses`
    - `chains: [{ chain_id, chain_name, address_type, tokens: [{symbol,address,decimals,min_checkout_usd}] }]`
    - `trading_balance`, `positions_value`, `redeem_value`
    - `account_ready`, `onboarding_state`, `onboarding_complete`.

- `GET /api/v1/user/wallets/funding-status`
  - Peut rester temporairement pour compat frontend, mais doit devenir un simple status compte/depot/trading balance.
  - Retourner les anciens champs avec valeurs neutres si necessaire (`needs_usdc=false`, `needs_eth=false`, `bridge_available=false`, `bridgeable_usdc=0`) pour eviter la casse pendant migration.
  - Ne jamais lancer d'auto-bridge.

- Supprimer/rendre legacy:
  - `POST /funding-force`: supprime/410/no-op, mais ne doit jamais envoyer de tx.
  - `GET /bridge-history`: plus utile si on ne tracke plus d'auto-bridge local.

## DB/migrations

- Ne pas casser les DB existantes. Ajouter colonnes idempotentes dans `bootstrap.py`.
- Conserver les anciennes colonnes au debut pour compat/backfill, mais ne plus s'en servir pour la logique critique.
- Les anciennes colonnes `funding_*` peuvent rester en DB mais deviennent legacy.
- `FundingTransaction` peut rester en compat, mais ne doit plus etre alimente par de l'auto-bridge.

## Transition / compatibilite users existants

Ce point est critique: il faut migrer l'etat actuel sans perte de donnees et sans casser les users existants.

- Ne supprimer aucune table existante.
- Ne drop aucune colonne existante.
- Ne vider aucune table.
- Ne reset aucun wallet/user/session.
- Ne pas casser les users qui ont deja:
  - un `wallet_address` Privy;
  - un `proxy_address` Safe;
  - des `clob_api_*`;
  - `onboarding_state=ready`;
  - `onboarding_complete=true`;
  - un `funding_deposit_address_cached`;
  - des `FundingTransaction`;
  - une session history;
  - une active/running session.
- Garder `FundingTransaction` et `/bridge-history` en lecture legacy si possible, mais ne plus afficher ca comme flow principal et ne plus creer de nouvelles rows via auto-bridge.
- Backfill:
  - si `wallet.funding_deposit_address_cached` existe, copier vers `deposit_address_evm` si `deposit_address_evm` est vide;
  - ne jamais ecraser une adresse de depot deja stockee;
  - si `deposit_address_svm` est vide, la recuperer via `bridge_client.create_deposit_addresses(proxy_address)`;
  - si API Polymarket renvoie plusieurs address types, stocker tout ce qui existe sans detruire l'ancien.
- Existing users avec `onboarding_complete=true` doivent rester complets.
- Existing users avec `onboarding_state=ready` mais `onboarding_complete=false` doivent pouvoir entrer dans l'app et voir le nouveau Deposit flow.
- Existing users qui ont du USDC/ETH sur leur wallet Arbitrum ne doivent pas etre auto-bridged. L'app ne doit pas toucher ces fonds.
- Existing users avec une session running:
  - ne pas interrompre la session pendant migration;
  - ne plus soft-stop a cause d'USDC/ETH Arbitrum apres deploy;
  - continuer les stops normaux SL/target volume/duree/manual.
- Frontend doit accepter les anciennes reponses temporairement pendant le rollout, mais privilegier les nouveaux champs `deposit_config`, `account_ready`, `trading_balance`.
- Si un ancien endpoint est garde, le marquer legacy dans le code/commentaires et l'empecher d'executer des actions dangereuses.
- Ne pas faire de "cleanup" destructif dans ce patch. Le nettoyage de tables/colonnes legacy sera une migration separee plus tard.

## Fees / builder code

- Le Rust service passe deja `builder_code` a la config CLOB; verifier que `POLYMARKET_BUILDER_CODE` est bien documente/requis en env.
- Supprimer la dependance produit aux wallets Arbitrum pour payer les fees.
- Builder fee cible produit: 40 bps. Donc environ $40 de fee pour $10,000 de volume.
- Pour les stats informatives, les builder fees theoriques peuvent etre estimees a 40 bps du volume:
  - `estimated_builder_fees = volume * 0.004` car 40 bps = 0.40%.
  - $10,000 volume => $40 estimated builder fees.
  - Ces fees sont statistiques/estimatives uniquement, pas des paiements executes par l'app.
  - Ne jamais alimenter `fee_queue`, ne jamais appeler `pay_fee`, ne jamais envoyer `transfer_usdc`.
  - UI wording si affiche: "Estimated builder fees" ou "Est. builder fees", jamais "Fees paid by app".
  - Ne PAS soustraire ces estimated builder fees du PnL/net PnL car Polymarket reflete deja les builder fees dans le PnL/balance.
  - Anciennes sessions: remettre l'affichage fees a 0 / ne plus afficher les anciennes fees applicatives comme cout actif.
  - Nouvelles sessions: `paid_fees = 0`, pas de fee app.
- Stopper:
  - `session_manager.launch_session` USDC/ETH Arbitrum guards.
  - `session_engine._check_arbitrum_fee_buffer`.
  - auto-bridge funding.
  - frontend copy reserve/gas.
  - `fee_service` paiements/queue.
- Aucun `pay_fee`, aucun `transfer_usdc`, aucune queue de fee active.
- Referral: garder les champs/referrer si souhaite, mais ne pas payer automatiquement 10% et ne pas promettre de payout actif.

## Fee stats / analytics decision

Decision produit:

- Plus aucune fee applicative.
- Les anciennes sessions doivent repartir avec fees affichees a 0 pour eviter la confusion produit.
- Les builder fees Polymarket sont deja refletees dans le PnL/balance Polymarket; ne pas les deduire une deuxieme fois du net PnL.
- Si on veut afficher une info fee, afficher uniquement une estimation indicative a 40 bps du volume, sans impact sur PnL.

Implementation recommandee:

- Ajouter une constante backend explicite, par exemple `BUILDER_FEE_BPS = 40`, ou reutiliser une config env si deja presente.
- Creer un helper unique pour eviter les divergences:
  - `estimate_builder_fee(volume_usdc) = round(volume_usdc * BUILDER_FEE_BPS / 10_000, 6)`.
- Remplacer les affichages/aggregations de `paid_fees` par 0 ou par `estimated_builder_fees` purement informatif selon le contexte:
  - global metrics;
  - strategy summary;
  - slot performance;
  - market history;
  - version performance;
  - daily volume/fees chart;
  - daily CPM/net CPM;
  - session summaries si UI affiche fees/net pnl.
- Garder les colonnes DB `paid_fees` et anciens snapshots pour audit/compat, mais ne pas les incrementer pour nouvelles sessions et ne pas les afficher comme cout actif.
- Pour ancienne data: ne pas afficher les anciennes fees applicatives; mettre fees a 0 dans l'UI/analytics produit, sauf vue legacy explicite si un admin en a besoin.
- Ne pas appeler cette valeur "paid" si elle est estimee. Utiliser `estimated_builder_fees`.
- Si API/DB doivent rester compat avec le front actuel:
  - garder `paid_fees=0` en reponse;
  - ajouter `estimated_builder_fees` et `fees_are_estimated=true` si cette info est utile.
- Recommendation propre: ajouter `estimated_builder_fees`, garder `paid_fees=0/legacy`, et garder `net_pnl = realized_pnl` car builder fees sont deja refletees par Polymarket.

## Reserve pct / builder fee recommendation

Garder `reserve_pct` pour l'instant, mais changer son sens produit:

- Avant: reserve fee wallet / reserve pour payer les fees app.
- Maintenant: capital cushion non deploye pour absorber les builder fees Polymarket, les arrondis, les fills partiels, les positions ouvertes et eviter que le bot tombe en "not enough balance" trop vite.
- Recommendation simple et robuste:
  - garder `reserve_pct` configurable;
  - garder la formule actuelle `deployable = gross * (1 - reserve_pct / 100)`;
  - renommer l'UI/copy vers "Capital buffer" ou "Execution buffer";
  - ne plus l'appeler "fee reserve";
  - ne plus decrementer ce reserve via `fee_reserve_used` applicatif;
  - les builder fees sont prises par Polymarket/CLOB, donc le vrai cash baisse dans le compte Polymarket, pas dans un wallet Arbitrum.

Pourquoi le garder:

- Avec 40 bps builder fee, un bot qui fait beaucoup de volume peut consommer rapidement du cash libre.
- Exemple: $10k volume = $40 fees. Si le bot recycle le capital plusieurs fois, le solde disponible peut baisser meme si les positions ne sont pas perdantes.
- Sans buffer, le moteur peut calculer des lots sur un capital theorique trop haut et finir par recevoir des erreurs "not enough balance".
- Le plus simple et moins risque est de garder une marge non deployee plutot que de tenter une comptabilite parfaite des fees builder.

Alternatives possibles, classees du plus simple au plus robuste:

1. Keep reserve pct as execution buffer.
   - Simple.
   - Aucun gros refactor runtime.
   - Evite beaucoup d'erreurs balance.
   - UX claire: "we keep X% unallocated for fees/slippage/execution safety".

2. Dynamic deployable refresh.
   - Poller le trading balance Polymarket pendant la session.
   - Si cash disponible baisse, reduire progressivement le capital alloue aux slots ou bloquer nouvelles buys.
   - Plus precis, mais plus complexe: il faut eviter de casser les lots ouverts et les replacements.

3. Fee-aware runtime accounting.
   - Estimer les builder fees par fill et reduire `deployable_capital`/available capital interne.
   - Moyen terme.
   - Risque d'ecart avec la vraie fee Polymarket si fee side/market/calcul change.

4. Cash-available preflight before placing buys.
   - Avant de placer un buy, demander le pUSD balance CLOB et limiter l'ordre.
   - Robuste mais ajoute des calls et peut ralentir le moteur.
   - Peut etre combine avec option 1.

Recommendation a implementer dans ce patch:

- Option 1 maintenant.
- Ajouter plus tard option 4 si les logs montrent encore des "not enough balance".
- Ne pas faire option 2/3 dans ce patch sauf si le code a deja les bons hooks.

Verifier les noms DB/UI:

- DB peut garder `session_fee_reserve` pour compat, mais UI ne doit plus dire "fees".
- Runtime summary peut garder `fee_reserve_remaining` pour compat frontend, mais idealement ajouter `capital_buffer` / `capital_buffer_remaining` plus tard.
- `paid_fees` doit etre cache ou affiche a 0 comme legacy, pas comme fee app active.

## Frontend attendu

`frontend/src/components/user/OnboardingModal.tsx`:
- Flow simplifie:
  1. account setup Polymarket direct depuis wallet Privy/Arbitrum;
  2. referral optionnel, mais sans promesse de payout auto;
  3. username si necessaire;
  4. deposit direct via adresse Polymarket avec select chain + token.
- Depot skippable.
- Si trading balance devient > 0, marquer onboarding complete.
- Ne plus demander ETH/USDC sur Arbitrum pour fees.

`frontend/src/components/user/ProfileTab.tsx`:
- Remplacer Deposit tab par depot direct Polymarket:
  - select chain: Arbitrum, Solana, Base;
  - select token selon chain;
  - afficher adresse EVM ou SVM correspondante;
  - afficher min deposit/minCheckoutUsd;
  - afficher trading balance Polymarket + positions/redeem;
  - bouton copy address.
- Supprimer textes reserve/gas/auto bridge.

`frontend/src/pages/UserDashboard.tsx`:
- Top menu: ne plus afficher Wallet USDC/ETH comme exigence produit.
- Supprimer banners `needs_usdc/needs_eth`.
- Low balance banner doit pointer vers Profile/Deposit Polymarket.

Admin:
- `frontend/src/pages/admin/Users.tsx`: les colonnes Wallet USDC/ETH/POL n'ont plus d'interet produit. Les garder seulement si debug admin explicitement utile, sinon supprimer pour simplifier.
- Supprimer warnings "no USDC/no ETH" dans admin launch.
- Action `Bridge POL`: legacy/debug uniquement. Si pas utile, supprimer.

FAQ/copy:
- `frontend/src/components/user/FaqTab.tsx`: remplacer la FAQ bridge/reserve Arbitrum.
- `frontend/src/pages/admin/Strategies.tsx`: copy `reserve_pct` "USDC buffer for fees" a revoir. Si `reserve_pct` reste, le presenter comme capital non deploye / cushion de session, pas comme fee reserve.

## Fichiers a inspecter/modifier

Backend:
- `backend/app/api/auth.py`
- `backend/app/api/user/wallets.py`
- `backend/app/integrations/polymarket/bridge_client.py`
- `backend/app/services/funding_service.py`
- `backend/app/services/balance_poller.py`
- `backend/app/runtime/session_manager.py`
- `backend/app/runtime/session_engine.py`
- `backend/app/services/fee_service.py`
- `backend/app/services/fee_payment_service.py`
- `backend/app/services/privy_tx_service.py`
- `backend/app/db/models.py`
- `backend/app/db/bootstrap.py`
- `backend/app/api/admin/users.py`
- `backend/app/api/admin/sessions.py`
- `backend/app/core/config.py`

Frontend:
- `frontend/src/components/user/OnboardingModal.tsx`
- `frontend/src/components/user/ProfileTab.tsx`
- `frontend/src/components/user/BridgeHistoryModal.tsx`
- `frontend/src/pages/UserDashboard.tsx`
- `frontend/src/utils/api.ts`
- `frontend/src/types/index.ts`
- `frontend/src/utils/bridge.ts`
- `frontend/src/utils/depositMonitor.ts`
- `frontend/src/components/user/FaqTab.tsx`
- `frontend/src/pages/admin/Users.tsx`
- `frontend/src/pages/admin/Sessions.tsx`
- `frontend/src/pages/admin/Strategies.tsx`

Rust:
- Ne modifier que si necessaire.
- Verifier que `builder_code` reste passe dans `Config::builder().builder_code`.
- Ne pas casser `proxy_address` comme funder/Safe.

## Validation attendue

- Backend demarre.
- Frontend build.
- Login new user:
  - wallet Privy cree/recupere;
  - Safe/proxy/CLOB pret;
  - account ready sans demander ETH/USDC Arbitrum.
- Onboarding/depot:
  - depot skippable;
  - affiche Arbitrum/Base/Solana;
  - affiche ETH/USDC/SOL selon chain;
  - affiche adresse EVM ou SVM correcte;
  - affiche min deposit issu de Polymarket bridge assets;
  - ne lance aucun transfert automatique;
  - `onboarding_complete=true` seulement quand `trading_balance > 0`.
- Existing user:
  - anciennes colonnes ne cassent pas le chargement;
  - `funding_deposit_address_cached` peut backfill EVM.
  - `FundingTransaction` legacy reste lisible si endpoint conserve.
  - un user ancien avec `onboarding_complete=true` reste complete.
  - un user ancien avec `onboarding_state=ready` mais sans depot accede a l'app et voit le nouveau Deposit flow.
- Session launch:
  - ne bloque plus sur wallet USDC/ETH;
  - bloque encore si trading balance Polymarket zero/insuffisante;
  - running session ne soft-stop plus a cause d'USDC/ETH Arbitrum;
  - stop uniquement SL/target volume/duree/manual/error.
- Fee system:
  - aucune tx Privy fee n'est envoyee;
  - `fee_queue` n'est plus alimentee;
  - referral reward n'est plus paye automatiquement.

## Questions restantes pour le dev

1. `reserve_pct`: aujourd'hui il retient une partie du capital de session: `fee_reserve = gross * reserve_pct / 100`, `deployable = gross - fee_reserve`. C'etait presente comme reserve fees. Maintenant soit on le garde comme cushion de securite/capital non deploye, soit on le met a 0/renomme. A trancher avec le produit.
2. Faut-il retirer visuellement les XP tiers/fee bps/free-fee puisqu'il n'y a plus de fee applicative?
3. Faut-il garder la campagne referral visible mais sans promettre de payout, ou la masquer temporairement?
4. Le capital lance doit-il etre limite au `trading_balance` disponible moins positions/redeem?
5. Faut-il stocker supported assets en DB/cache ou les recuperer a chaque demande?
6. Pour Arbitrum et Base, meme adresse EVM? Probablement oui via `address.evm`; confirmer par API.
7. Pour Solana, utiliser `address.svm`; confirmer par API.
8. `/status/{deposit_address}` existe dans `bridge_client.get_transaction_status`; interet potentiel: voir si Polymarket bridge detecte/traite un depot. Si l'API retourne des txs exploitables, on peut afficher un status. Sinon, ne pas poller et se contenter du trading balance.

## Dependency map a verifier dans le moindre detail

## Second-pass obligatoire: effets domino a ne pas rater

Cette migration n'est pas juste un changement d'onboarding. Elle touche tous les endroits ou l'ancien modele "fee app + wallet Arbitrum reserve + bridge backend" influencait l'etat produit, les stats, les guards, l'admin et les copies UI.

Points retrouves par recherche globale a traiter explicitement:

- `backend/app/services/recovery_service.py`
  - Rebuild les sessions running apres restart avec `user_fee_bps`, `fee_is_overridden`, `fee_reserve`, `fee_wallet_address`.
  - Si `SessionEngine` garde des params legacy, recovery peut les passer a 0/None, mais ne doit pas relancer un moteur qui recommence a charger des fees.
  - Les sessions running migrees/recovered ne doivent pas soft-stop a cause d'Arbitrum USDC/ETH.
  - `slot_runtime._paid_fees` est restaure depuis DB: garder historique, mais ne pas continuer a l'incrementer.

- `backend/app/services/analytics_service.py`
  - `global_metrics`, `strategy_summary`, `slot_performance`, `market_history_performance`, `version_performance`, `daily_volume`, `daily_cpm` agregent encore `paid_fees`.
  - Decision recommandee: garder les champs API pour compat mais les valeurs nouvelles restent 0/legacy; changer UI/copy pour ne plus vendre ca comme fees applicatives actives.
  - `net_cpm = pnl - fees` devient historique seulement. Si plus de fees applicatives, le "net" n'inclut pas automatiquement les builder fees Polymarket sauf si le PnL CLOB les reflete deja. Ne pas inventer une estimation sans source fiable.

- `backend/app/api/admin/strategy.py` et `backend/app/services/strategy_service.py`
  - `fee_side` et `new_user_free_fee_days` sont toujours dans les schemas/create/update/details.
  - Garder DB/API legacy si besoin pour compat, mais UI admin ne doit plus permettre de configurer une fee side active.
  - Si l'API accepte encore `fee_side`, forcer/normaliser a `"none"` ou ignorer la valeur pour eviter resurrection accidentelle des fees.

- `backend/app/api/admin/users.py`
  - `free_fee_days` modifie `free_fee_ends_at` et pousse `engine._user_fee_bps`.
  - A desactiver/legacy: plus de free-fee produit puisqu'il n'y a plus de fee app.
  - `list_users` expose `wallet_usdc`, `wallet_eth`, `effective_fee_bps`, `free_fee_ends_at`, `onboarding_complete`.
  - Ajouter `account_ready` dans la reponse admin si possible pour separer compte technique pret vs funded.
  - `bridge_pol` utilise `relay_bridge_service`; garder seulement debug admin avance ou supprimer UI.

- `backend/app/services/relay_bridge_service.py`
  - Bridge `$1 USDC Arbitrum -> POL Polygon` est une logique gas legacy.
  - Ne doit pas etre reachable depuis le parcours user.
  - Si garde, admin-only debug avec wording clair, pas une action onboarding/funding.

- `backend/app/main.py`
  - Verifier startup: si `fee_service.start(...)` existe, ne plus le demarrer.
  - Shutdown doit rester safe si le service n'a pas demarre.
  - `balance_poller` doit rester car trading balance/positions sont encore utiles.

- `frontend/src/store/index.ts`
  - Store garde `fundingStatus: any`.
  - Renommer plus tard en `depositStatus` serait propre, mais pour ce patch on peut garder `fundingStatus` compat en y mettant les nouveaux champs depot/trading balance.

- `frontend/src/types/index.ts`
  - Il existe deja des types `BridgeDepositAddresses`, `BridgeDepositConfig`, `DepositTracker`.
  - Les adapter/reutiliser au lieu de creer des types doublons.
  - Mettre a jour les commentaires `fee_bps`, `wallet_usdc`, `wallet_eth`, `paid_fees` comme legacy/debug si les champs restent.

- `frontend/src/components/strategy/StrategyCard.tsx`
  - Texte actuel: "{reserve_pct}% of capital held as fee reserve."
  - Changer en "capital/execution buffer".

- `frontend/src/components/session/ActiveSessionCard.tsx`
  - Calcule `netPnl = grossPnl - paid_fees` et affiche "Fees Paid".
  - A cacher/renommer legacy; sinon l'utilisateur verra des fees app a 0 et une UI incoherente.

- `frontend/src/components/session/SessionLaunchForm.tsx`
  - Utilise `reservePct = strategy.reserve_pct`.
  - Copy/label doit devenir execution buffer, pas fee reserve.
  - Le calcul peut rester.

- `frontend/src/components/user/RunTab.tsx`
  - Affiche `fees = rt?.paid_fees`.
  - Utilise `positionsValue = fundingStatus?.positions_value`.
  - Verifier capital max: ne pas permettre un capital qui depasse le cash Polymarket disponible si `trading_balance` inclut positions.

- `frontend/src/components/user/HistoryTab.tsx`
  - Affiche `paid_fees` et net pnl historique.
  - Garder historique possible, mais label legacy/hide pour nouvelles sessions.

- `frontend/src/components/user/ProfileModal.tsx`
  - Affiche `fee_bps` et "decreases with volume".
  - A retirer/masquer si XP tiers ne baissent plus de fees.

- `frontend/src/components/user/XPModal.tsx`
  - Tiers XP bases sur `fee_bps`.
  - Soit masquer tout le module XP fee, soit redefinir XP comme volume/statut sans benefit fee.

- `frontend/src/components/user/ReferralTab.tsx` et `ReferralHistoryModal.tsx`
  - Enlever toute promesse type "earn 10% trading fees credited instantly in USDC".
  - `referral_reward_usdc` peut rester historique.

- `frontend/src/pages/admin/Sessions.tsx`
  - Affiche "Fees Paid" et `Net PnL = gross - paid_fees`.
  - Liste utilisateurs eligible avec warnings `wallet_usdc < 2`, `wallet_eth < 0.0005`.
  - Retirer ces warnings; eligible = account ready + trading cash suffisant + pas de session active + strategy OK.

- `frontend/src/pages/admin/Strategies.tsx`
  - Settings contiennent `fee_side`, `new_user_free_fee_days`, free-fee users.
  - UI a retirer ou mettre legacy cachee.
  - `reserve_pct` copy a convertir en execution buffer.

- `frontend/src/pages/admin/Users.tsx`
  - UI free-fee/fee override encore active.
  - Colonnes wallet USDC/ETH colorent en rouge/jaune selon seuils legacy.
  - `onboardedOnly` filtre `onboarding_complete`, qui devient funded et non account-ready.
  - Ajouter/afficher deux etats si possible: `account_ready` et `funded`.

- `frontend/src/utils/api.ts`
  - `forceFunding`, `getBridgeHistory`, `getFundingStatus` existent.
  - Ajouter `getDepositConfig`.
  - Garder `getFundingStatus` compat si backend le garde.
  - Retirer les appels UI a `forceFunding`.

- `frontend/src/utils/bridge.ts` et `frontend/src/utils/depositMonitor.ts`
  - Deja presents; verifier s'ils sont faits pour l'ancien bridge ou le nouveau tracker deposit.
  - Nettoyer seulement ce qui est inutile, sans casser localStorage existant.

Regle generale:

- Les anciennes valeurs `paid_fees`, `fee_bps`, `free_fee_ends_at`, `fee_side`, `wallet_usdc`, `wallet_eth`, `funding_state`, `FundingTransaction` peuvent rester en schema/DB pour compat et historique.
- Mais aucune de ces valeurs ne doit encore:
  - bloquer un launch;
  - arreter une session;
  - declencher un transfert;
  - promettre une reward/payout;
  - etre affichee comme action produit principale.

### Backend auth/onboarding

- `backend/app/api/auth.py`
  - `UserProfile.onboarding_complete` est lu par frontend; ne pas changer son meaning sans ajouter `account_ready`.
  - `_run_onboarding_background` doit continuer a stocker `proxy_address` et CLOB creds.
  - Le precache deposit doit passer de `funding_deposit_address_cached` a nouvelles colonnes EVM/SVM sans ecraser l'ancien.
  - Le free-fee auto applique dans onboarding doit probablement etre retire/desactive ou laisse legacy sans UI.
  - `sync_user` retry onboarding si `pending/failed`: verifier que le nouveau flow ne relance pas sans fin pour users existants.
  - `get_me` doit retourner assez d'infos pour savoir account ready vs funded.
  - `submit_clob_creds` doit rester si encore necessaire pour CLOB creds browser/residential IP.

### Backend wallet/deposit/funding

- `backend/app/api/user/wallets.py`
  - `_onboarding_checks`: adapter labels account setup, pas funding.
  - `_deposit_seed_address`: garder `proxy_address` comme seed Polymarket.
  - `_funding_thresholds`: legacy uniquement ou supprimer de flow principal.
  - `_ensure_cached_deposit_address`: remplacer par multi-address cache EVM/SVM.
  - `WalletOut.usdc_balance`: verifier s'il doit devenir trading balance uniquement.
  - `FundingStatusOut`: garder compat neutre ou creer `DepositConfigOut`.
  - `funding_status`: doit stopper auto-bridge, stop latest tx detection, stop timeout bridging, stop `transfer_usdc`.
  - `funding_force`: no-op/410/legacy, jamais tx.
  - `bridge_history`: legacy read-only ou endpoint retire du frontend.
  - Tous les messages doivent retirer "send ETH", "gas", "reserve", "Bridge to Polymarket".

- `backend/app/integrations/polymarket/bridge_client.py`
  - `get_supported_assets`: verifier shape exacte de l'API.
  - `create_deposit_addresses`: verifier shape exacte `address.evm`, `address.svm`.
  - `get_transaction_status`: ne pas utiliser si non necessaire; pas de polling inutile.
  - Ajouter helper de normalisation si ca evite de dupliquer la logique frontend.

- `backend/app/services/funding_service.py`
  - `get_balances`, `get_usdc_balance`, `get_eth_balance`, `get_balances_batch`: plus necessaires produit user, mais peuvent rester pour debug admin.
  - `get_trading_balance`: garder utile.
  - `compute_reserve_target`, `compute_warn_threshold`, `compute_bridge_amount`: legacy, ne plus appeler.
  - `get_latest_usdc_deposit_tx`: legacy, ne plus appeler.

- `backend/app/services/balance_poller.py`
  - Continue a poll `trading_balance`, `positions_value`, `redeem_value`.
  - Arbitrum USDC/ETH/POL peut rester admin debug, mais ne doit plus alimenter user warnings/session guards.
  - `get_arb()` ne doit plus etre utilise par session launch/monitor.
  - Verifier que si `proxy_address` absent, positions/trading restent safe.

### Backend sessions/runtime

- `backend/app/runtime/session_manager.py`
  - Retirer bloc balance guards Arbitrum.
  - Garder one active session per user.
  - Garder strategy published/global pause.
  - Garder viability check.
  - Ajouter/verifier guard capital <= available trading cash.
  - Message d'erreur doit dire "Deposit to your Polymarket account", pas Arbitrum/bridge.
  - `fee_reserve` calcule a partir `reserve_pct`: garder comme buffer, renommer comments/copy si possible.
  - `_start_runtime` ne doit plus passer de referrer payout utile.
  - `fee_wallet_address=wallet.wallet_address` devient inutile pour fees; verifier si engine peut accepter vide.

- `backend/app/runtime/session_engine.py`
  - `_monitor_loop`: retirer `_check_arbitrum_fee_buffer`.
  - `_check_arbitrum_fee_buffer`: supprimer ou laisser unused.
  - `_on_lot_opened/_on_lot_closed`: ne plus appeler `fee_service.process_*` ou `fee_service` no-op.
  - `charge_fee`: legacy only; ne doit plus etre appele.
  - `_check_session_sl`: actuellement `total_loss = fee_reserve_used + abs(min(0, realized_pnl))`; si fee_reserve_used reste 0, OK. Confirmer que SL ne doit pas inclure builder fees estimees.
  - `summary.fee_reserve_used/paid_fees`: UI legacy/hide.
  - `_increment_user_volume`: garder XP volume? Si XP cache, peut rester volume analytics.

- `backend/app/runtime/session_slot_runtime.py`
  - Les "not enough balance" errors peuvent arriver plus souvent si builder fees consomment cash.
  - Recommandation future: preflight balance or reduce orders when repeated balance errors.
  - Ne pas refactorer le moteur maintenant sauf necessaire.
  - Verifier `POLY_MIN_ORDER_USDC = 5`; min order reste independant depot.

### Backend fees/referral

- `backend/app/services/fee_service.py`
  - Ne plus demarrer ou rendre no-op.
  - `_enqueue` ne doit plus ecrire `fee_queue`.
  - `_flush_loop` ne doit plus payer.
  - Existing rows dans `fee_queue`: ne pas supprimer; ne pas payer automatiquement.

- `backend/app/services/fee_payment_service.py`
  - Ne plus etre appele.
  - `pay_fee` legacy only.

- `backend/app/services/privy_tx_service.py`
  - `transfer_usdc` ne doit plus etre appele par funding/fee.
  - Peut rester pour admin legacy si necessaire, mais pas flow user.

- `backend/app/api/user/profile.py`
  - Verifier referrer copy/data.
  - `referral_reward_usdc` historique: garder mais ne plus promettre future auto payout.

- `backend/app/api/admin/analytics.py`
  - `referral_leaderboard` peut devenir historique only.
  - `total_fees`, `fees_paid` analytics doivent etre caches/renommes/legacy.

### Backend admin

- `backend/app/api/admin/users.py`
  - `list_users`: wallet_usdc/eth/pol debug only.
  - `onboarding_complete` devient funded; ajouter `account_ready` si admin doit filtrer compte pret.
  - `bridge_pol`: supprimer UI ou legacy admin only.
  - `reregister_clob`, `migrate_v2`, `nuclear_close`, `poly-state`: garder.
  - Bug a surveiller: code utilise `wallet_obj.safe_address` alors model a `proxy_address`; verifier si existant bug hors scope.

- `backend/app/api/admin/sessions.py`
  - `launch-for-user` depend de `session_manager.launch_session`; apres retrait guards, OK.
  - Admin launch eligibility frontend doit changer.

- `backend/app/db/bootstrap.py`
  - Ajouter colonnes deposit addresses.
  - Ne pas drop funding columns.
  - `ensure_fee_queue_table`: peut rester pour compat, mais ne pas demarrer fee flush.
  - `reset_stale_funding_states`: verifier si encore utile ou no-op.

- `backend/app/main.py`
  - Ne plus demarrer `fee_service.start(...)` si no fee.
  - Continuer `balance_poller.start()`.
  - Continuer `bridge_client.init()`.
  - Shutdown: ne pas appeler `fee_service.stop()` si non demarre, ou rendre safe.

### Frontend onboarding/deposit

- `frontend/src/components/user/OnboardingModal.tsx`
  - Step account setup doit poll `onboarding-status`.
  - Step deposit doit appeler `deposit-config`, pas `funding-force`.
  - Supprimer progress bridge deposit/bridge/trading si plus de bridge backend.
  - Remplacer par selector chain/token + address + min deposit + copy.
  - Poll trading balance optionnel pour detecter completion.
  - Skip for now doit rester.
  - LocalStorage skip key peut rester mais ne doit pas masquer forever si trading_balance > 0.
  - Referral step copy doit enlever "10% fees automatically" si plus de payout.

- `frontend/src/components/user/ProfileTab.tsx`
  - Remove Arbitrum wallet deposit instructions.
  - Remove gas/reserve/auto bridge text.
  - Remove Bridge button or rename Deposit.
  - Remove Bridge ready row.
  - Wallet balances USDC/ETH no longer important.
  - Keep withdraw instructions? Re-evaluate.

- `frontend/src/components/user/BridgeHistoryModal.tsx`
  - Remove from imports/usages if no history.
  - If kept, rename "Legacy bridge history" and read-only.

- `frontend/src/pages/UserDashboard.tsx`
  - Remove wallet USDC/ETH in `UserMenu` or move to debug.
  - Remove needs/warn funding banners.
  - Keep low trading balance banner pointing to Deposit.
  - Funding refresh interval should be based on trading balance/account status, not needs_usdc/eth.

- `frontend/src/utils/api.ts`
  - Add `getDepositConfig`.
  - Remove/legacy `forceFunding`.
  - Remove/legacy `getBridgeHistory`.
  - `getFundingStatus` compat if backend keeps it.

- `frontend/src/types/index.ts`
  - Add deposit config types.
  - Mark Bridge types legacy or remove if unused.
  - AdminUser may need `account_ready`.

- `frontend/src/utils/bridge.ts`
  - Many helpers become unused if no history/status. Remove or legacy.

- `frontend/src/utils/depositMonitor.ts`
  - LocalStorage tracker likely obsolete unless used for selected chain/token. Remove or repurpose carefully.

- `frontend/src/components/user/FaqTab.tsx`
  - Rewrite bridge system answer.
  - Explain direct Polymarket deposit addresses and skip.

- `frontend/src/components/user/ReferralTab.tsx`
  - Remove promise "credited instantly in USDC to Arbitrum wallet" or mark paused/coming soon.

- `frontend/src/components/user/ReferralHistoryModal.tsx`
  - Same copy update.

### Frontend admin

- `frontend/src/pages/admin/Users.tsx`
  - Remove/redesign wallet USDC/ETH/POL columns or mark debug.
  - Remove color warnings tied to thresholds.
  - Remove `Bridge POL` action if not useful.
  - Add account ready/funded separation if backend provides it.

- `frontend/src/pages/admin/Sessions.tsx`
  - Admin launch eligible users: remove no USDC/no ETH warnings.
  - Filter only no active session + wallet_id + trading_balance >= min.
  - Error copy should mention Polymarket balance only.

- `frontend/src/pages/admin/Strategies.tsx`
  - `reserve_pct` help text must become "capital buffer" not "USDC buffer for fees".

### Rust service

- `polymarket_rust_service/src/live.rs`
  - Builder code already included. Verify no changes needed.
  - If builder fee causes smaller effective available balance, Rust order placement may return rejections; Python runtime should handle gracefully.

- `polymarket_rust_service/src/main.rs`
  - Wallet balance endpoint returns pUSD on Polygon; still useful for trading balance.

### Docs/env

- `README.md`
  - Update funding/onboarding docs eventually.
  - Mark old env vars deprecated.

- `backend/.env.production`
  - Do not delete vars in this patch unless requested.
  - Ensure `POLYMARKET_BUILDER_CODE` set.

## Questions produit/tech a poser avant implementation finale

### Onboarding et etats

1. Est-ce que `onboarding_state=ready` doit etre renomme cote UI en `Account ready`, pendant que `onboarding_complete` reste reserve au depot?
2. Si un user skip le depot, doit-on garder un flag localStorage comme aujourd'hui ou stocker en DB `deposit_skipped_at`?
3. Quand `trading_balance > 0`, doit-on automatiquement fermer la modale onboarding meme si le user n'a pas choisi username/referrer?
4. Un user existant avec `onboarding_complete=false` mais `trading_balance > 0` doit-il etre backfill complete au prochain `funding-status/deposit-config`?
5. Si `onboarding_state=failed` mais le user a deja un `proxy_address` et des creds CLOB, doit-on tenter un recovery ou seulement afficher retry account setup?

### Depot Polymarket

6. Veux-tu afficher une seule adresse a la fois selon chain/token, ou un tableau de toutes les adresses possibles?
7. Pour EVM, est-ce que Base et Arbitrum doivent afficher la meme adresse EVM si l'API renvoie une seule `address.evm`?
8. Pour Solana, est-ce que `address.svm` doit etre affiche meme si le wallet utilisateur est EVM/Privy? Probablement oui car c'est une adresse de depot Polymarket, pas son wallet.
9. Faut-il afficher un warning "send only selected token on selected chain"?
10. Faut-il afficher le token contract address pour USDC Base/Arbitrum, ou seulement symbol + chain + min deposit?
11. Si Polymarket supported-assets renvoie plus de chains/tokens que ceux demandes, faut-il cacher strictement tout sauf Arb/Base/Solana et ETH/USDC/SOL?
12. Si l'API ne renvoie pas `minCheckoutUsd` pour ETH/SOL, quel fallback afficher?
13. Faut-il garder un bouton "Refresh trading balance" apres depot?
14. Faut-il poller seulement `trading_balance` toutes les X secondes dans la modale depot jusqu'a > 0?
15. Si on poll trading balance, quelle frequence? 5s, 10s, 30s?
16. Faut-il afficher un texte "depot peut prendre quelques minutes" ou eviter toute promesse?

### Ancien bridge/funding

17. `/bridge-history` doit-il rester accessible quelque part en "legacy history" pour les anciens depots auto-bridge, ou totalement cache UI?
18. `funding-force`: tu preferes 410 Gone clair, ou no-op qui retourne le nouveau funding-status pour compat?
19. Les anciennes colonnes `funding_state`, `funding_tx_hash`, etc. doivent-elles etre laissees telles quelles sans update, ou normalisees a `legacy`/`idle`?
20. Si un wallet est actuellement `funding_state=bridging`, faut-il le laisser visible en legacy ou reset a idle au prochain boot comme aujourd'hui?
21. Le code `reset_stale_funding_states` doit-il rester, etre no-op, ou seulement toucher les anciens states dangereux?

### Sessions / capital

22. Le launch doit-il autoriser `gross_capital` jusqu'au trading balance total incluant positions, ou seulement cash disponible hors positions?
23. Aujourd'hui `trading_balance` expose parfois `available + positions_value`. Pour launch, il faut probablement utiliser seulement le cash disponible. Confirmer.
24. Si un user a `trading_balance=100` mais `positions_value=80`, peut-il lancer une session de 100 ou seulement 20?
25. Faut-il empecher plusieurs sessions simultanees comme aujourd'hui? Probablement oui.
26. Une session running existante avec ancien `fee_reserve` doit-elle garder son `fee_reserve` historique ou etre ignoree visuellement?

### Fees / XP / referrals

27. Doit-on cacher toutes les mentions de "fees paid", "fee reserve", "free fee", "XP fee" dans user/admin?
28. Si on garde XP tiers, ils representent quoi maintenant sans fee applicative?
29. Referral: garder l'onglet referral visible mais remplacer la promesse "10% fees" par "coming soon"?
30. `referral_reward_usdc` existant doit-il rester affiche comme historique, ou masque?
31. Les anciennes rewards deja creditees doivent-elles rester en DB et UI?
32. `fee_queue` doit-elle etre creee au startup encore, ou on peut garder la table sans demarrer `fee_service.start()`?
33. Si on ne demarre plus `fee_service`, faut-il drainer/laisser les rows existantes dans `fee_queue` sans paiement?

### Admin / operations

34. Colonnes admin Wallet USDC/ETH/POL: debug seulement. Tu veux les garder dans admin users ou les enlever pour eviter confusion?
35. Action admin `Bridge POL`: utile uniquement pour gas Polygon legacy/redeem. Tu veux la garder en menu admin avance ou supprimer?
36. Admin launch modal: faut-il filtrer eligible users uniquement sur `trading_balance >= 5`, sans regarder USDC/ETH?
37. Admin user filter "onboarded only": doit-il filtrer `onboarding_complete` depot, ou `account_ready` compte technique?
38. Dans admin, faut-il afficher deux badges separes: `account ready` et `funded`?

### Env / rollout

39. `.env.production`: garder les vars legacy `FUNDING_*`, `FEE_WALLET_*`, `BRIDGE_MIN_USDC`, `ARBITRUM_*` pour rollback, ou commenter comme deprecated plus tard?
40. Faut-il ajouter une feature flag pour nouveau depot flow, ou migrer directement?
41. Faut-il garder compat frontend/backend pendant un deploy progressif, ou deploy full stack simultane?
42. En cas d'echec API Polymarket `/supported-assets`, doit-on afficher une erreur bloquante ou fallback adresse + "min unavailable"?
43. En cas d'echec API `/deposit`, doit-on retry ou afficher support Discord?
44. Quelle telemetry/logs veux-tu pour savoir si un user a vu/copied une deposit address?

### Effets domino / compat

45. Doit-on retirer completement le concept "free fee" de l'admin, ou le laisser visible comme legacy non editable?
46. Si un admin modifie encore `fee_override_bps` via API directe, faut-il ignorer la valeur, la stocker mais ne rien appliquer, ou retourner 410/400?
47. Decision: pour les analytics historiques, remettre/afficher les fees applicatives a 0 pour eviter confusion. Garder data DB seulement audit/legacy non visible.
48. Decision: `Net PnL` devient simplement le PnL realise/expose par Polymarket/runtime. Ne pas soustraire les estimated builder fees.
49. Decision: les builder fees Polymarket sont deja refletees dans le PnL/balances, donc ne pas les compter une deuxieme fois. Les 40 bps sont informatifs seulement si affiches.
50. Le `reserve_pct` doit-il etre renomme partout en UI seulement, ou aussi dans nouveaux champs API (`capital_buffer_pct`) tout en gardant DB legacy?
51. Dans DB, ajouter `account_ready` comme champ persiste ou le calculer a la volee (`onboarding_state=ready && proxy_address && clob creds`)?
52. Dans admin "onboarded only", tu veux filtrer les comptes prets a trader techniquement (`account_ready`) ou les comptes funds (`onboarding_complete`)?
53. Faut-il garder les balances Arbitrum USDC/ETH visibles en admin debug si elles ne servent plus, ou les retirer pour eviter que l'operateur croie qu'il faut les remplir?
54. `relay_bridge_service.bridge_pol_for_gas`: suppression UI seulement ou endpoint backend aussi rendu 410?
55. Existing rows dans `fee_queue`: doit-on les laisser sans traitement, les marquer cancelled legacy, ou les garder intactes pour audit?
56. Recovery de sessions actives: faut-il garder le `session_fee_reserve` historique comme buffer runtime, ou recalculer avec le nouveau sens `capital_buffer` au restart?
57. Decision: si une session active a ete lancee avant la migration avec fee app active, elle doit continuer sans nouvelles fees applicatives des le deploy.
58. Decision: les anciennes sessions dans History ne doivent plus afficher leurs anciennes fees comme cout produit; afficher 0 ou masquer.
59. `ProfileModal`/XP: veux-tu supprimer les tiers de fees maintenant ou les remplacer par des tiers purement reputation/volume?
60. Decision: referral garde la logique/referrer/DB, mais rien ne se passe financierement et aucun payout/reward n'est base sur builder fee.
61. Nouveau endpoint `deposit-config`: doit-il etre appele au login pour pre-cache les adresses, ou seulement quand l'utilisateur ouvre onboarding/profile deposit?
62. Si `create_deposit_addresses` est lent/echec, doit-on permettre quand meme l'app et afficher "deposit temporarily unavailable"?
63. Pour Solana USDC, veux-tu afficher explicitement "USDC on Solana" pour eviter confusion avec USDC EVM?
64. Pour ETH sur Base/Arbitrum, faut-il avertir que ce n'est pas WETH mais native ETH si l'API le confirme?
65. Faut-il afficher QR code pour les adresses depot, ou copy address suffit?
66. Est-ce qu'un depot de token non-USDC/ETH/SOL supporte par Polymarket doit etre impossible dans UI meme si API le renvoie?
67. Decision: `onboarding_complete` signifie "a deja fund au moins une fois"; ne pas repasser false si le user retire ensuite.
68. Decision: ajouter/exposer un etat live separe `is_funded_now = trading_balance > 0` et utiliser surtout `trading_cash_available` pour les launch guards.
69. Si un user a `trading_balance > 0` mais account setup pas ready (`proxy_address` absent), doit-on marquer funded mais bloquer launch avec "account setup pending"?
70. Faut-il backfill `onboarding_complete=true` uniquement au prochain status call, ou faire une migration startup qui lit balances pour tous les users? Recommande: lazy backfill au status pour eviter gros I/O.

## Contraintes

- Ne pas casser la creation Safe/CLOB/approvals.
- Ne pas supprimer des colonnes existantes sans migration sure.
- Ne pas casser les sessions running: une session active doit continuer meme si UI/API funding change.
- Ne jamais envoyer de transaction USDC automatique depuis Privy wallet pour bridge ou fees dans le nouveau flow.
- Garder le style UI existant, sobre, dense.
- Apres changement, executer au minimum:
  - backend import/syntax check;
  - frontend `npm run build`;
  - si Rust modifie, `cargo build`.
