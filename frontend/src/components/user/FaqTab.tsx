import { useState, useRef, useEffect } from 'react'
import { ChevronDown } from 'lucide-react'

const FAQS = [
  {
    q: 'How does the bridge system works?',
    a: 'You deposit USDC along with a small amount of ETH on Arbitrum. The ETH is used to cover gas fees related to platform transactions, while your USDC is used for trading. When your available USDC exceeds the reserve threshold, the excess is automatically bridged to your Polymarket trading balance. A portion remains on Arbitrum as a reserve to ensure fees can continue to be processed smoothly. If the reserve becomes too low, your session will pause. You can resume at any time by topping up your Arbitrum wallet with a bit more ETH or USDC.'
  },
  {
    q: 'Is my capital safe? Who controls my funds?',
    a: 'Your funds always remain in your own Polymarket wallet, and you are the only one with access to your private key. We never take custody of your capital. The system simply executes trades on your behalf using your API credentials, directly on Polymarket. At any time, you are free to withdraw your funds yourself from Polymarket.',
  },
  {
    q: 'How is the platform fee calculated?',
    a: 'The fee is based on your trading volume. As your lifetime volume grows, your rate gradually decreases. You can always check your current fee level and upcoming thresholds in the Profile section. Fees are applied as trades are executed, not at the end of a session.',
  },
  {
    q: 'What does the system actually do, and is there risk involved?',
    a: 'The system places buy orders slightly below the market price and then sells at a predefined spread. This is a market making approach designed to capture small price differences. Market conditions can move at any time, and factors such as price changes, execution timing, and fees can lead to losses. Only allocate capital you are comfortable using.',
  },
  {
    q: 'Why does the system show "recovering" after a restart?',
    a: 'When the system starts, it retrieves your open orders and balances from Polymarket and rebuilds its internal state. This usually takes a few seconds. If it takes longer than expected, you can contact support for assistance.',
  },
  {
    q: 'How do I withdraw my funds?',
    a: 'You can withdraw directly from Polymarket at any time. From the Profile section, follow the instructions to export your private key, import it into Rabby Wallet, and connect to Polymarket. Since you fully control your wallet, withdrawals are always initiated by you.',
  },
  {
    q: 'What is CPM and how should I understand it?',
    a: 'CPM represents your net performance scaled to one million dollars of trading volume. It helps standardise results across sessions of different sizes and durations, making comparisons easier. A positive CPM indicates profitability at scale, while a negative CPM suggests that fees or losses are outweighing gains.',
  },
  {
    q: 'Why does my balance sometimes drop or seem lower than expected?',
    a: 'Some of your positions may have resolved as winners on Polymarket. When a market resolves YES and you hold YES shares, the winnings are not automatically added to your trading balance — Polymarket requires you to manually claim them. Until you do, that capital stays locked and unavailable for the bot to use, which reduces your effective trading balance. To claim your winnings, go to polymarket.com, connect with your wallet, and look for any resolved positions to collect. We recommend doing this regularly to keep your full capital available.',
  },
]

function DiscordIcon({ size = 16 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
      <path d="M20.317 4.492c-1.53-.69-3.17-1.2-4.885-1.49a.075.075 0 0 0-.079.036c-.21.369-.444.85-.608 1.23a18.566 18.566 0 0 0-5.487 0 12.36 12.36 0 0 0-.617-1.23A.077.077 0 0 0 8.562 3c-1.714.29-3.354.8-4.885 1.491a.07.07 0 0 0-.032.027C.533 9.093-.32 13.555.099 17.961a.08.08 0 0 0 .031.055 20.03 20.03 0 0 0 5.993 2.98.078.078 0 0 0 .084-.026c.462-.62.874-1.275 1.226-1.963a.075.075 0 0 0-.041-.104 13.201 13.201 0 0 1-1.872-.878.075.075 0 0 1-.008-.125c.126-.093.252-.19.372-.287a.075.075 0 0 1 .078-.01c3.927 1.764 8.18 1.764 12.061 0a.075.075 0 0 1 .079.009c.12.098.245.195.372.288a.075.075 0 0 1-.006.125c-.598.344-1.22.635-1.873.877a.075.075 0 0 0-.041.105c.36.687.772 1.341 1.225 1.962a.077.077 0 0 0 .084.028 19.963 19.963 0 0 0 6.002-2.981.076.076 0 0 0 .032-.054c.5-5.094-.838-9.52-3.549-13.442a.06.06 0 0 0-.031-.028zM8.02 15.278c-1.182 0-2.157-1.069-2.157-2.38 0-1.312.956-2.38 2.157-2.38 1.21 0 2.176 1.077 2.157 2.38 0 1.312-.956 2.38-2.157 2.38zm7.975 0c-1.183 0-2.157-1.069-2.157-2.38 0-1.312.955-2.38 2.157-2.38 1.21 0 2.176 1.077 2.157 2.38 0 1.312-.946 2.38-2.157 2.38z"/>
    </svg>
  )
}

function FaqItem({ q, a }: { q: string; a: string }) {
  const [open, setOpen] = useState(false)
  const contentRef = useRef<HTMLDivElement>(null)
  const [height, setHeight] = useState(0)

  useEffect(() => {
    if (contentRef.current) {
      setHeight(open ? contentRef.current.scrollHeight : 0)
    }
  }, [open])

  return (
    <div className="border-b border-white/[0.05] last:border-b-0">
      <button
        className="w-full text-left flex items-center justify-between gap-4 px-5 py-4 group"
        onClick={() => setOpen(v => !v)}
      >
        <span className="text-[12px] font-semibold text-white/70 group-hover:text-white/90 transition-colors leading-snug">{q}</span>
        <ChevronDown
          size={14}
          className={`flex-shrink-0 text-white/25 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
        />
      </button>
      <div
        ref={contentRef}
        style={{ height, overflow: 'hidden', transition: 'height 200ms ease' }}
      >
        <div className="px-5 pb-4">
          <p className="text-[11px] text-white/45 leading-relaxed">{a}</p>
        </div>
      </div>
    </div>
  )
}

const DISCORD_URL = 'https://discord.gg/y2B8kmAA3n'

export function FaqTab() {
  return (
    <div className="p-5 space-y-5">
      <div className="space-y-1">
        <p className="text-[11px] font-semibold text-white/50">Frequently asked questions</p>
        <p className="text-[10px] text-white/25">Security, performance, and how the bot works.</p>
      </div>

      <div className="rounded-xl border border-white/[0.07] bg-[#0d0e10] overflow-hidden divide-y divide-white/[0.05]">
        {FAQS.map(({ q, a }) => (
          <FaqItem key={q} q={q} a={a} />
        ))}
      </div>

      <div className="flex items-center gap-3 px-4 py-3 rounded-xl border border-white/[0.06] bg-white/[0.02]">
        <span className="text-[#c4254a]/70 flex-shrink-0"><DiscordIcon size={16} /></span>
        <span className="text-[11px] text-white/35">Still have questions?</span>
        <a
          href={DISCORD_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[11px] font-semibold text-[#c4254a]/70 hover:text-[#c4254a] transition-colors"
        >
          Join our Discord →
        </a>
      </div>
    </div>
  )
}
