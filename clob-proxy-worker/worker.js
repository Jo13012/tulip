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

    const reqHeaders = new Headers(request.headers)
    reqHeaders.set('Accept-Encoding', 'identity')

    const proxiedRequest = new Request(targetUrl, {
      method: request.method,
      headers: reqHeaders,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
    })

    const response = await fetch(proxiedRequest)

    const newHeaders = new Headers(response.headers)
    newHeaders.set('Access-Control-Allow-Origin', '*')
    newHeaders.delete('Content-Encoding')

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: newHeaders,
    })
  },
}
