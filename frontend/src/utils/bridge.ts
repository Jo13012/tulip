import type {
  BridgeDepositAddresses,
  BridgeSupportedAsset,
  BridgeTransactionStatusItem,
} from '../types'

export const WALLET_BALANCE_REFRESH_EVENT = 'polybot:wallet-balance-refresh'
export const FUNDING_STATUS_REFRESH_MS = 10000

export interface BridgeChainOption {
  chainId: string
  chainName: string
  addressType: keyof BridgeDepositAddresses
  tokens: Array<{
    tokenAddress: string
    symbol: string
    tokenDecimals: number
  }>
  minCheckoutUsd: number
}

export function getAddressTypeForChain(chainName: string): keyof BridgeDepositAddresses {
  const value = chainName.toLowerCase()
  if (value.includes('solana')) return 'svm'
  return 'evm'
}

export function groupBridgeChains(assets: BridgeSupportedAsset[]): BridgeChainOption[] {
  const grouped = new Map<string, BridgeChainOption>()

  for (const asset of assets) {
    const key = `${asset.chainId}:${asset.chainName}`
    const existing = grouped.get(key)
    const token = {
      tokenAddress: asset.token.address,
      symbol: asset.token.symbol,
      tokenDecimals: asset.token.decimals,
    }

    if (!existing) {
      grouped.set(key, {
        chainId: asset.chainId,
        chainName: asset.chainName,
        addressType: getAddressTypeForChain(asset.chainName),
        tokens: [token],
        minCheckoutUsd: asset.minCheckoutUsd,
      })
      continue
    }

    if (!existing.tokens.some((item) => item.tokenAddress.toLowerCase() === token.tokenAddress.toLowerCase())) {
      existing.tokens.push(token)
    }
    existing.minCheckoutUsd = Math.min(existing.minCheckoutUsd, asset.minCheckoutUsd)
  }

  return Array.from(grouped.values())
}

export function formatUsd(value: number | null | undefined) {
  if (value == null) return '--'
  return value.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export function shortenAddress(value: string, start = 6, end = 4) {
  if (!value) return '--'
  if (value.length <= start + end) return value
  return `${value.slice(0, start)}...${value.slice(-end)}`
}

export function formatBridgeStatus(status: string | undefined) {
  const value = (status || '').toUpperCase()
  if (!value) return 'Awaiting deposit'
  if (value === 'DEPOSIT_DETECTED') return 'Deposit detected'
  if (value === 'PROCESSING') return 'Processing'
  if (value === 'COMPLETED' || value === 'GOOD' || value === 'SUCCESS') return 'Completed'
  if (value === 'FAILED' || value === 'ERROR' || value === 'REJECTED') return 'Failed'
  return value.replace(/_/g, ' ').toLowerCase().replace(/^\w/, (c) => c.toUpperCase())
}

export function isBridgeSuccessStatus(status: string | undefined) {
  const value = (status || '').toUpperCase()
  return value === 'COMPLETED' || value === 'GOOD' || value === 'SUCCESS'
}

export function isBridgeFailureStatus(status: string | undefined) {
  const value = (status || '').toUpperCase()
  return value === 'FAILED' || value === 'ERROR' || value === 'REJECTED' || value === 'CANCELLED'
}

export function isBridgePendingStatus(status: string | undefined) {
  return !isBridgeSuccessStatus(status) && !isBridgeFailureStatus(status)
}

export function pickRelevantBridgeTransaction(
  transactions: BridgeTransactionStatusItem[],
  selectedChainId: string,
) {
  const relevant = transactions.filter((tx) => tx.fromChainId === selectedChainId)
  if (!relevant.length) return null

  const pending = relevant
    .filter((tx) => isBridgePendingStatus(tx.status))
    .sort((a, b) => (Number(b.createdTimeMs || 0) - Number(a.createdTimeMs || 0)))

  if (pending.length) return pending[0]

  return relevant.sort((a, b) => (Number(b.createdTimeMs || 0) - Number(a.createdTimeMs || 0)))[0]
}

export function formatBaseUnitAmount(baseUnit: string | undefined, decimals: number | undefined) {
  if (!baseUnit || decimals == null) return null

  const normalized = baseUnit.replace(/^0+/, '') || '0'
  if (decimals <= 0) return Number(normalized)

  const padded = normalized.padStart(decimals + 1, '0')
  const whole = padded.slice(0, -decimals)
  const fraction = padded.slice(-decimals).replace(/0+$/, '')
  const output = fraction ? `${whole}.${fraction.slice(0, 6)}` : whole
  return Number(output)
}

export function buildExplorerUrl(chainName: string, txHash: string | undefined) {
  if (!txHash) return ''

  const chain = chainName.toLowerCase()
  if (chain.includes('solana')) return `https://solscan.io/tx/${txHash}`
  if (chain.includes('base')) return `https://basescan.org/tx/${txHash}`
  if (chain.includes('arbitrum')) return `https://arbiscan.io/tx/${txHash}`
  if (chain.includes('ethereum')) return `https://etherscan.io/tx/${txHash}`
  return ''
}
