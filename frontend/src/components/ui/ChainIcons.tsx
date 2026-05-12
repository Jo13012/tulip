// Chain and token SVG icons — accurate brand marks, 32×32 viewBox

export function ArbitrumIcon({ size = 24, className = '' }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
      <circle cx="16" cy="16" r="16" fill="#1B2535"/>
      {/* Arbitrum 'A' stylised mark */}
      <path d="M16 6.5L8 11v10l8 4.5 8-4.5V11L16 6.5z" stroke="#96BEDC" strokeWidth="1" fill="none" strokeLinejoin="round"/>
      <path d="M13 20l3-8 3 8" stroke="#96BEDC" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
      <path d="M12.5 18.5h7" stroke="#96BEDC" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  )
}

export function BaseIcon({ size = 24, className = '' }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
      <circle cx="16" cy="16" r="16" fill="#0052FF"/>
      {/* Base official circle-B */}
      <path
        d="M16 7.5C11.306 7.5 7.5 11.306 7.5 16S11.306 24.5 16 24.5c4.326 0 7.916-3.16 8.446-7.337H16v-2.326h10.457C26.046 10.52 21.45 7.5 16 7.5z"
        fill="white"
      />
    </svg>
  )
}

export function SolanaIcon({ size = 24, className = '' }: { size?: number; className?: string }) {
  // Unique gradient IDs per render to avoid collisions across multiple instances
  const id = 'sol_pt'
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
      <circle cx="16" cy="16" r="16" fill="#000"/>
      {/* Solana three-bar mark — parallelogram bars */}
      <path d="M8.5 21.5h12l3-2.5H11.5L8.5 21.5z" fill={`url(#${id}1)`}/>
      <path d="M8.5 16.75h12l3-2.5H11.5L8.5 16.75z" fill={`url(#${id}2)`}/>
      <path d="M8.5 12h12l3-2.5H11.5L8.5 12z" fill={`url(#${id}3)`}/>
      <defs>
        <linearGradient id={`${id}1`} x1="8.5" y1="20.25" x2="23.5" y2="20.25" gradientUnits="userSpaceOnUse">
          <stop stopColor="#9945FF"/><stop offset="1" stopColor="#14F195"/>
        </linearGradient>
        <linearGradient id={`${id}2`} x1="8.5" y1="15.5" x2="23.5" y2="15.5" gradientUnits="userSpaceOnUse">
          <stop stopColor="#9945FF"/><stop offset="1" stopColor="#14F195"/>
        </linearGradient>
        <linearGradient id={`${id}3`} x1="8.5" y1="10.75" x2="23.5" y2="10.75" gradientUnits="userSpaceOnUse">
          <stop stopColor="#9945FF"/><stop offset="1" stopColor="#14F195"/>
        </linearGradient>
      </defs>
    </svg>
  )
}

export function USDCIcon({ size = 24, className = '' }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
      <circle cx="16" cy="16" r="16" fill="#2775CA"/>
      {/* USDC official $ inside circle mark */}
      <path d="M16 7.5C11.306 7.5 7.5 11.306 7.5 16S11.306 24.5 16 24.5 24.5 20.694 24.5 16 20.694 7.5 16 7.5z" stroke="white" strokeWidth=".8" fill="none"/>
      <path d="M18.8 18.12c0-1.58-1.04-2.08-3.1-2.32-1.46-.17-1.72-.6-1.72-1.3 0-.7.56-1.16 1.66-1.16.98 0 1.54.33 1.8 1.12a.34.34 0 0 0 .34.22h.7c.2 0 .36-.16.36-.36a2.76 2.76 0 0 0-2.5-2.27v-1.2c0-.2-.16-.36-.36-.36h-.68c-.2 0-.36.16-.36.36v1.18c-1.44.2-2.42 1.16-2.42 2.48 0 1.5.98 2.06 3.04 2.32 1.38.18 1.82.52 1.82 1.4 0 .88-.66 1.42-1.74 1.42-1.36 0-1.82-.58-1.98-1.38a.34.34 0 0 0-.34-.26h-.74c-.2 0-.36.16-.36.36.22 1.38 1.08 2.24 2.76 2.48v1.2c0 .2.16.36.36.36h.68c.2 0 .36-.16.36-.36v-1.18c1.52-.2 2.52-1.24 2.52-2.66z" fill="white"/>
    </svg>
  )
}

export function USDTIcon({ size = 24, className = '' }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
      <circle cx="16" cy="16" r="16" fill="#26A17B"/>
      {/* USDT T mark with bar */}
      <path d="M11 9.5h10v2.5H17.3v1.5c2.7.12 4.7.65 4.7 1.3s-2 1.18-4.7 1.3v4.4h-2.6v-4.4c-2.7-.12-4.7-.65-4.7-1.3s2-1.18 4.7-1.3V12H11V9.5z" fill="white"/>
    </svg>
  )
}

export function ETHIcon({ size = 24, className = '' }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
      <circle cx="16" cy="16" r="16" fill="#627EEA"/>
      {/* ETH diamond mark */}
      <path d="M16 5.5v8.44l7.13 3.18L16 5.5z" fill="white" fillOpacity=".6"/>
      <path d="M16 5.5L8.87 17.12 16 13.94V5.5z" fill="white"/>
      <path d="M16 23.1v3.4l7.13-9.85L16 23.1z" fill="white" fillOpacity=".6"/>
      <path d="M16 26.5V23.1l-7.13-5.55L16 26.5z" fill="white"/>
      <path d="M16 21.8l7.13-4.14L16 14.5v7.3z" fill="white" fillOpacity=".2"/>
      <path d="M8.87 17.66L16 21.8V14.5l-7.13 3.16z" fill="white" fillOpacity=".6"/>
    </svg>
  )
}

export function SOLIcon({ size = 24, className = '' }: { size?: number; className?: string }) {
  return <SolanaIcon size={size} className={className} />
}

// USDC.e — same blue as USDC but with a small "e" badge overlay
export function USDCeIcon({ size = 24, className = '' }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" className={className}>
      <circle cx="16" cy="16" r="16" fill="#2775CA"/>
      <path d="M16 7.5C11.306 7.5 7.5 11.306 7.5 16S11.306 24.5 16 24.5 24.5 20.694 24.5 16 20.694 7.5 16 7.5z" stroke="white" strokeWidth=".8" fill="none"/>
      <path d="M18.4 17.9c0-1.45-.96-1.9-2.86-2.12-1.34-.16-1.58-.55-1.58-1.19 0-.64.52-1.07 1.52-1.07.9 0 1.42.3 1.66 1.03a.31.31 0 0 0 .31.2h.64c.18 0 .33-.15.33-.33a2.54 2.54 0 0 0-2.3-2.08v-1.1c0-.18-.15-.33-.33-.33h-.62c-.18 0-.33.15-.33.33v1.08c-1.32.18-2.22 1.07-2.22 2.28 0 1.38.9 1.9 2.8 2.14 1.27.16 1.67.48 1.67 1.28 0 .81-.61 1.3-1.6 1.3-1.25 0-1.67-.53-1.82-1.27a.31.31 0 0 0-.31-.24h-.68c-.18 0-.33.15-.33.33.2 1.27.99 2.06 2.54 2.28v1.1c0 .18.15.33.33.33h.62c.18 0 .33-.15.33-.33v-1.08c1.4-.18 2.32-1.14 2.32-2.44z" fill="white"/>
      {/* "e" subscript badge */}
      <circle cx="23" cy="23" r="5" fill="#1a5fa8"/>
      <text x="23" y="26.5" textAnchor="middle" fontSize="6.5" fontWeight="700" fill="white" fontFamily="sans-serif">e</text>
    </svg>
  )
}

export const CHAIN_ICON_META: Record<string, {
  label: string
  color: string
  borderColor: string
  Icon: React.FC<{ size?: number; className?: string }>
}> = {
  '42161': { label: 'Arbitrum', color: '#96bedc', borderColor: 'rgba(150,190,220,0.18)', Icon: ArbitrumIcon },
  '8453':  { label: 'Base',     color: '#4f83ff', borderColor: 'rgba(79,131,255,0.18)',  Icon: BaseIcon },
  'solana':{ label: 'Solana',   color: '#9945ff', borderColor: 'rgba(153,69,255,0.18)',  Icon: SolanaIcon },
}

export const TOKEN_ICON_MAP: Record<string, React.FC<{ size?: number; className?: string }>> = {
  USDC:   USDCIcon,
  'USDC.e': USDCeIcon,
  USDT:   USDTIcon,
  ETH:    ETHIcon,
  WETH:   ETHIcon,
  SOL:    SOLIcon,
}

// Fallback min deposits shown when backend returns null
export const TOKEN_MIN_FALLBACK: Record<string, string> = {
  USDC:     '$20',
  'USDC.e': '$20',
  USDT:     '$20',
  ETH:      '0.001 ETH',
  WETH:     '0.001 WETH',
  SOL:      '0.1 SOL',
}

// Static chain/token layout — used as fallback when depositConfig not yet loaded
// or to pre-populate the UI before the backend responds.
export const STATIC_DEPOSIT_CHAINS = [
  {
    chain_id: '42161',
    chain_name: 'Arbitrum',
    address_type: 'evm' as const,
    tokens: [
      { symbol: 'USDC',     address: '', decimals: 6,  min_checkout_usd: 20 },
      { symbol: 'USDC.e',   address: '', decimals: 6,  min_checkout_usd: 20 },
      { symbol: 'ETH',      address: '', decimals: 18, min_checkout_usd: null },
    ],
  },
  {
    chain_id: '8453',
    chain_name: 'Base',
    address_type: 'evm' as const,
    tokens: [
      { symbol: 'USDC', address: '', decimals: 6,  min_checkout_usd: 20 },
      { symbol: 'ETH',  address: '', decimals: 18, min_checkout_usd: null },
    ],
  },
  {
    chain_id: 'solana',
    chain_name: 'Solana',
    address_type: 'svm' as const,
    tokens: [
      { symbol: 'USDC', address: '', decimals: 6, min_checkout_usd: 20 },
      { symbol: 'USDT', address: '', decimals: 6, min_checkout_usd: 20 },
      { symbol: 'SOL',  address: '', decimals: 9, min_checkout_usd: null },
    ],
  },
]
