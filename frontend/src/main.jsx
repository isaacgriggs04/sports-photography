import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ClerkProvider } from '@clerk/clerk-react'
import './index.css'
import App from './App.jsx'

const runtimeKey = window?.__APP_CONFIG__?.clerkPublishableKey
const buildKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY
const PUBLISHABLE_KEY = runtimeKey || buildKey

if (!PUBLISHABLE_KEY) {
  createRoot(document.getElementById('root')).render(
    <div style={{ padding: '24px', fontFamily: 'sans-serif' }}>
      <h1>Configuration Error</h1>
      <p>Missing <code>VITE_CLERK_PUBLISHABLE_KEY</code>.</p>
      <p>Add it in Railway Variables and redeploy.</p>
    </div>,
  )
} else {
  createRoot(document.getElementById('root')).render(
    <StrictMode>
      <ClerkProvider publishableKey={PUBLISHABLE_KEY}>
        <App />
      </ClerkProvider>
    </StrictMode>,
  )
}
