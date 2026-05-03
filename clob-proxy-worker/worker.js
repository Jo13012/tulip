// Cloudflare Worker — proxy transparent vers clob.polymarket.com
// Déployer sur: https://dash.cloudflare.com/workers
// URL cible backend: https://clob-proxy.<ton-sous-domaine>.workers.dev

const TARGET = 'https://clob.polymarket.com'
const ALLOWED_PATHS = ['/auth/derive-api-key', '/auth/api-key', '/auth/ban-status']

export default {
  async fetch(request) {
    const url = new URL(request.url)

    // Sécurité: seulement les paths CLOB auth
    if (!ALLOWED_PATHS.some(p => url.pathname.startsWith(p))) {
      return new Response('Not found', { status: 404 })
    }

    const targetUrl = TARGET + url.pathname + url.search

    const proxiedRequest = new Request(targetUrl, {
      method: request.method,
      headers: request.headers,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
    })

    const response = await fetch(proxiedRequest)

    // Pass-through avec CORS pour le backend
    const newHeaders = new Headers(response.headers)
    newHeaders.set('Access-Control-Allow-Origin', '*')

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: newHeaders,
    })
  },
}
