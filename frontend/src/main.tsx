import React from 'react'
import ReactDOM from 'react-dom/client'
import { PrivyProvider } from '@privy-io/react-auth'
import App from './App'
import './index.css'

const PRIVY_APP_ID = import.meta.env.VITE_PRIVY_APP_ID as string

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <PrivyProvider
      appId={PRIVY_APP_ID}
      config={{
        loginMethods: ['email', 'google', 'twitter'],
        appearance: {
          theme: 'dark',
          accentColor: '#6366f1',
        },
        embeddedWallets: {
          ethereum: {
            createOnLogin: 'off',
          },
        },
      }}
    >
      <App />
    </PrivyProvider>
  </React.StrictMode>,
)
