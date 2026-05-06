// ── Auth ─────────────────────────────────────────────────────────────────────

export interface AppUser {
  user_id: string
  privy_user_id: string
  display_name: string
  can_change_username?: boolean
  referrer_username?: string | null
  referral_reward_usdc?: number
  is_admin: boolean
  wallet_id: string
  wallet_address: string
  onboarding_complete: boolean
  trading_volume?: number
  bot_pnl?: number
  fee_bps?: number
  fee_override_bps?: number | null
  free_fee_active?: boolean
  free_fee_ends_at?: string | null
}

// ── Profile ───────────────────────────────────────────────────────────────────

export interface ProfileData {
  trading_volume:    number
  bot_pnl:           number
  fee_bps:           number   // active fee: override ?? XP
  fee_is_overridden: boolean  // true when admin set a custom fee
  wallet_volume:     number   // always a number (fallback = trading_volume)
  wallet_pnl:        number   // always a number (fallback = bot_pnl)
  referrer_username: string | null
  can_set_referrer: boolean
  referral_reward_usdc: number
}

export interface ReferralListItem {
  user_id: string
  username: string
  reward_usdc: number
}

// ── XP ────────────────────────────────────────────────────────────────────────

export interface XPLevel {
  level:  number
  volume: number
  fee:    number
}

export const XP_LEVELS: XPLevel[] = [
  { level: 1,  volume: 0,           fee: 12   },
  { level: 2,  volume: 25_000,      fee: 11 },
  { level: 3,  volume: 75_000,      fee: 10   },
  { level: 4,  volume: 150_000,     fee: 9 },
  { level: 5,  volume: 300_000,     fee: 8   },
  { level: 6,  volume: 500_000,     fee: 7 },
  { level: 7,  volume: 750_000,     fee: 6   },
  { level: 8,  volume: 1_000_000,   fee: 5 },
  { level: 9,  volume: 1_500_000,   fee: 4 },
  { level: 10, volume: 2_000_000,   fee: 3   },
]

// ── Admin ─────────────────────────────────────────────────────────────────────

export interface AdminUser {
  id:                  string
  display_name:        string | null
  is_admin:            boolean
  is_active:           boolean
  fee_override_bps:    number | null   // null = no override, uses XP fee
  effective_fee_bps:   number          // XP-computed fee
  trading_volume:      number
  bot_pnl:             number
  wallet_address:      string | null
  proxy_address:       string | null
  onboarding_complete: boolean
  onboarding_completed_at: string | null
  wallet_usdc:         number | null   // live Arb USDC balance
  wallet_eth:          number | null   // live Arb ETH balance
  trading_balance:     number | null   // live Polymarket total (avail + positions + redeem)
  positions_value:     number          // open positions mark-to-market
  redeem_value:        number          // redeemable positions value
  active_session_id:   string | null
  session_status:      string | null
  session_live:        boolean
  created_at:          string
  free_fee_ends_at:    string | null
}

// ── Strategy ─────────────────────────────────────────────────────────────────

export interface Strategy {
  strategy_id: string | null
  available: boolean
  unavailable_reason: string | null
  global_pause: boolean
  reserve_pct: number
  version_label: string | null
  version_id: string | null
  estimate: StrategyEstimate | null
}

export interface StrategyEstimate {
  avg_daily_volume_multiple: number
  avg_pnl_pct: number
  sample_size: number
  disclaimer: string
}

export interface ViabilityResult {
  viable: boolean
  min_capital: number
  recommended_capital: number
  reason: string
}

// ── Session ──────────────────────────────────────────────────────────────────

export interface Session {
  session_id: string
  status: SessionStatus
  health_state: HealthState
  gross_capital: number
  deployable_capital: number
  fee_reserve: number
  stop_mode: 'days' | 'volume'
  stop_days: number | null
  target_volume: number | null
  session_sl_pct: number
  started_at: string | null
  stopped_at: string | null
  stop_reason: string | null
  runtime: RuntimeSummary | null
}

export interface RuntimeSummary {
  session_id: string
  health_state: HealthState
  status: string
  gross_capital: number
  deployable_capital: number
  fee_reserve_used: number
  fee_reserve_remaining: number
  total_deployed: number
  total_volume: number
  realized_pnl: number
  unrealized_pnl: number
  total_pnl: number
  paid_fees: number
  slot_count: number
  active_slot_count: number
  trade_count: number
  order_count: number
  uptime_s: number
}

export interface RuntimeOpenOrder {
  order_id: string
  price: number
  size: number
  side: 'BUY' | 'SELL'
}

export interface RuntimeLot {
  lot_id: string
  entry_price: number
  target_sell_price: number
  size: number
  tokens: number
  status: 'OPEN' | 'SELLING' | 'CLOSED'
  pnl: number
  timestamp_entry: string | null
}

export interface RuntimeSlotStats {
  session_slot_id: string
  state: string
  allocated_capital: number
  deployed_capital: number
  pending_buy_capital: number
  open_lots: number
  volume: number
  realized_pnl: number
  unrealized_pnl: number
  paid_fees: number
  trade_count: number
  question: string | null
  condition_id: string | null
  active_token_id: string | null
  polymarket_url: string | null
  side: string | null
  price_min: number | null
  price_max: number | null
  lot_size: number | null
  open_order_list: RuntimeOpenOrder[]
  sell_order_list: RuntimeOpenOrder[]
  lot_list: RuntimeLot[]
  bid_price: number
  pending_question: string | null
}

export type SessionStatus =
  | 'pending' | 'starting' | 'running'
  | 'stopping' | 'stopped' | 'error' | 'recovering'

export type HealthState =
  | 'starting' | 'running' | 'idle' | 'recovering'
  | 'degraded' | 'stopping' | 'stopped' | 'error'

// ── UI ───────────────────────────────────────────────────────────────────────

export interface LogEntry {
  level: string
  message: string
  slot_id: string
  timestamp: string
}

export interface ToastData {
  id: string
  type: 'success' | 'error' | 'warning' | 'info' | 'loading'
  message: string
  title?: string
  action_label?: string
  action_href?: string
  meta_lines?: string[]
  duration?: number
}

export interface BridgeSupportedToken {
  name: string
  symbol: string
  address: string
  decimals: number
}

export interface BridgeSupportedAsset {
  chainId: string
  chainName: string
  token: BridgeSupportedToken
  minCheckoutUsd: number
}

export interface BridgeDepositAddresses {
  evm: string
  svm: string
  btc: string
  tvm: string
}

export interface BridgeDepositConfig {
  walletId: string
  polymarketAddress: string
  depositAddresses: BridgeDepositAddresses
  supportedAssets: BridgeSupportedAsset[]
  usdcBalance: number | null
}

export interface BridgeTransactionStatusItem {
  status: string
  txHash?: string
  fromChainId?: string
  fromAmountBaseUnit?: string
  fromTokenAddress?: string
  toChainId?: string
  toTokenAddress?: string
  createdTimeMs?: number
}

export interface BridgeTransactionStatusResponse {
  transactions: BridgeTransactionStatusItem[]
}

export interface DepositTrackerToken {
  tokenAddress: string
  symbol: string
  tokenDecimals: number
}

export interface DepositTracker {
  userId: string
  depositAddress: string
  selectedChainId: string
  selectedChainName: string
  selectedAddressType: keyof BridgeDepositAddresses
  tokens: DepositTrackerToken[]
}
