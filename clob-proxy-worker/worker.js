const TARGET = 'https://clob.polymarket.com'
const ALLOWED_PATHS = ['/auth/derive-api-key', '/auth/api-key', '/auth/ban-status']

export default {
  async fetch(request) {
    const url = new URL(request.url)

    if (!ALLOWED_PATHS.some(p => url.pathname.startsWith(p))) {
      return new Response('Not found', { status: 404 })
    }

    const targetUrl = TARGET + url.pathname + url.search

    const reqHeaders = new Headers(request.headers)
    reqHeaders.set('Accept-Encoding', 'identity')
    reqHeaders.delete('cf-connecting-ip')
    reqHeaders.delete('cf-ray')
    reqHeaders.delete('cf-visitor')
    reqHeaders.delete('x-forwarded-for')
    reqHeaders.delete('x-real-ip')

    const proxiedRequest = new Request(targetUrl, {
      method: request.method,
      headers: reqHeaders,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
    })

    const response = await fetch(proxiedRequest)

    // Decompress body fully, re-serve as plain JSON
    const text = await response.text()

    const newHeaders = new Headers()
    newHeaders.set('Content-Type', 'application/json')
    newHeaders.set('Access-Control-Allow-Origin', '*')

    return new Response(text, {
      status: response.status,
      statusText: response.statusText,
      headers: newHeaders,
    })
  },
}
