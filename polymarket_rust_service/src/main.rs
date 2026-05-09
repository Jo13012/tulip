mod config;
mod live;
mod models;
mod privy_auth;
mod privy_signer;

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use config::Settings;
use dashmap::DashMap;
use live::LiveClientHandle;
use models::{
    CancelOrderRequest, ClientIdRequest, ClientOpenRequest, ClientOpenResponse,
    EnsureSellAllowanceRequest, OrderBookResponse, OrderStatusRequest,
    PlaceLimitOrderRequest, ResolveMarketResponse, TokenBalanceRequest,
};
use reqwest::Client;
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{net::SocketAddr, sync::Arc};
use tower_http::{cors::CorsLayer, trace::TraceLayer};
use tracing::{error, info};
use uuid::Uuid;

// pUSD on Polygon — V2 collateral token (replaces USDC.e from 2026-04-28)
const PUSD_POLYGON: &str = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB";

#[derive(Clone)]
struct AppState {
    settings: Settings,
    http: Client,
    clients: Arc<DashMap<String, Arc<LiveClientHandle>>>,
}

#[derive(Debug, thiserror::Error)]
enum AppError {
    #[error("{0}")]
    BadRequest(String),
    #[error("{0}")]
    Internal(String),
}

impl IntoResponse for AppError {
    fn into_response(self) -> axum::response::Response {
        let status = match self {
            AppError::BadRequest(_) => StatusCode::BAD_REQUEST,
            AppError::Internal(_) => StatusCode::INTERNAL_SERVER_ERROR,
        };
        (status, Json(json!({ "detail": self.to_string() }))).into_response()
    }
}

type AppResult<T> = Result<T, AppError>;

#[derive(Deserialize)]
struct ResolveQuery {
    url: String,
}

#[derive(Deserialize)]
struct EventQuery {
    slug: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    dotenvy::dotenv().ok();

    tracing_subscriber::fmt()
        .with_env_filter(
            std::env::var("RUST_LOG")
                .unwrap_or_else(|_| "polymarket_rust_service=info,tower_http=info".to_string()),
        )
        .init();

    let settings = Settings::from_env();
    let state = AppState {
        settings: settings.clone(),
        http: Client::builder().use_rustls_tls().build().expect("http client"),
        clients: Arc::new(DashMap::new()),
    };

    let app = Router::new()
        .route("/health", get(health))
        .route("/api/v1/resolve-market", get(resolve_market))
        .route("/api/v1/resolve-event", get(resolve_event))
        .route("/api/v1/book/{token_id}", get(book))
        .route("/api/v1/midpoint/{token_id}", get(midpoint))
        .route("/api/v1/best-bid-ask/{token_id}", get(best_bid_ask))
        .route("/api/v1/client/open", post(client_open))
        .route("/api/v1/client/close", post(client_close))
        .route("/api/v1/client/usdc-balance", post(client_usdc_balance))
        .route("/api/v1/client/token-balance", post(client_token_balance))
        .route("/api/v1/client/ensure-sell-allowance", post(client_ensure_sell_allowance))
        .route("/api/v1/client/place-limit-order", post(client_place_limit_order))
        .route("/api/v1/client/open-orders", post(client_open_orders))
        .route("/api/v1/client/order-status", post(client_order_status))
        .route("/api/v1/client/cancel-order", post(client_cancel_order))
        .route("/api/v1/wallet-balance/{address}", get(wallet_usdc_balance))
        .layer(CorsLayer::permissive())
        .layer(
            TraceLayer::new_for_http()
                .on_failure(tower_http::trace::DefaultOnFailure::new().level(tracing::Level::DEBUG))
        )
        .with_state(state);

    let addr: SocketAddr = format!("{}:{}", settings.host, settings.port).parse()?;
    info!("polymarket rust service listening on {addr} [live mode]");
    axum::serve(tokio::net::TcpListener::bind(addr).await?, app).await?;
    Ok(())
}

async fn health(State(state): State<AppState>) -> Json<Value> {
    Json(json!({ "status": "ok", "mode": "live", "version": "0.1.0" }))
}

// ── Market resolution ─────────────────────────────────────────────────────────

async fn resolve_market(
    State(state): State<AppState>,
    Query(query): Query<ResolveQuery>,
) -> AppResult<Json<ResolveMarketResponse>> {
    let slug = extract_slug(&query.url)?;
    let data = state
        .http
        .get(format!("{}/markets", state.settings.gamma_host))
        .query(&[("slug", slug.clone())])
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("gamma request failed: {e}")))?
        .json::<Value>()
        .await
        .map_err(|e| AppError::Internal(format!("gamma decode failed: {e}")))?;

    let markets = if let Some(arr) = data.as_array() {
        arr.clone()
    } else {
        data.get("markets").and_then(|v| v.as_array()).cloned().unwrap_or_default()
    };

    let market = markets
        .iter()
        .find(|m| m.get("slug").and_then(Value::as_str) == Some(slug.as_str()))
        .ok_or_else(|| AppError::BadRequest(format!("No market found for slug '{slug}'")))?;

    let token_ids = parse_token_ids(market);
    if token_ids.len() < 2 {
        return Err(AppError::BadRequest("Market has fewer than 2 token ids".to_string()));
    }

    Ok(Json(ResolveMarketResponse {
        question: market.get("question").and_then(Value::as_str).unwrap_or(&slug).to_string(),
        condition_id: market.get("conditionId").and_then(Value::as_str).unwrap_or_default().to_string(),
        token_id_yes: token_ids[0].clone(),
        token_id_no: token_ids[1].clone(),
        tick_size: market.get("minimumTickSize")
            .map(|v| v.as_str().map(|s| s.to_string()).unwrap_or_else(|| v.to_string()))
            .unwrap_or_else(|| "0.001".to_string()),
        neg_risk: market.get("negRisk").and_then(Value::as_bool).unwrap_or(false),
        active: market.get("active").and_then(Value::as_bool).unwrap_or(true),
        closed: market.get("closed").and_then(Value::as_bool).unwrap_or(false),
        image: market.get("image").or_else(|| market.get("icon"))
            .and_then(Value::as_str).unwrap_or_default().to_string(),
    }))
}

// ── Event sub-market resolution ───────────────────────────────────────────────

async fn resolve_event(
    State(state): State<AppState>,
    Query(query): Query<EventQuery>,
) -> AppResult<Json<Value>> {
    // Extract slug from URL or use as-is
    let slug = if query.slug.contains('/') {
        extract_slug(&query.slug)?
    } else {
        query.slug.clone()
    };

    let data = state
        .http
        .get(format!("{}/events", state.settings.gamma_host))
        .query(&[("slug", slug.as_str())])
        .send()
        .await
        .map_err(|e| AppError::Internal(format!("gamma events request failed: {e}")))?
        .json::<Value>()
        .await
        .map_err(|e| AppError::Internal(format!("gamma events decode failed: {e}")))?;

    let events = if let Some(arr) = data.as_array() {
        arr.clone()
    } else {
        return Err(AppError::BadRequest(format!("No event found for slug '{slug}'")));
    };

    let event = events
        .iter()
        .find(|e| e.get("slug").and_then(Value::as_str) == Some(slug.as_str()))
        .ok_or_else(|| AppError::BadRequest(format!("No event found for slug '{slug}'")))?;

    let markets = event.get("markets").and_then(Value::as_array).cloned().unwrap_or_default();

    let sub_markets: Vec<Value> = markets.iter().filter_map(|m| {
        let token_ids = parse_token_ids(m);
        if token_ids.len() < 2 { return None; }

        let question = m.get("question").and_then(Value::as_str).unwrap_or("").to_string();
        let market_slug = m.get("slug").and_then(Value::as_str).unwrap_or("").to_string();
        let condition_id = m.get("conditionId").and_then(Value::as_str).unwrap_or("").to_string();
        let closed = m.get("closed").and_then(Value::as_bool).unwrap_or(false);
        let active = m.get("active").and_then(Value::as_bool).unwrap_or(true);

        // Build the full polymarket URL for the sub-market
        let event_slug = event.get("slug").and_then(Value::as_str).unwrap_or(&slug);
        let url = format!("https://polymarket.com/event/{}/{}", event_slug, market_slug);

        Some(json!({
            "question": question,
            "slug": market_slug,
            "url": url,
            "condition_id": condition_id,
            "token_id_yes": token_ids[0],
            "token_id_no": token_ids[1],
            "tick_size": m.get("minimumTickSize").map(|v| v.as_str().map(|s| s.to_string()).unwrap_or_else(|| v.to_string())).unwrap_or_else(|| "0.001".to_string()),
            "neg_risk": m.get("negRisk").and_then(Value::as_bool).unwrap_or(false),
            "active": active,
            "closed": closed,
        }))
    }).collect();

    Ok(Json(json!({
        "event_slug": slug,
        "title": event.get("title").and_then(Value::as_str).unwrap_or(&slug),
        "sub_markets": sub_markets,
    })))
}

// ── Orderbook ─────────────────────────────────────────────────────────────────

async fn book(State(state): State<AppState>, Path(token_id): Path<String>) -> AppResult<Json<OrderBookResponse>> {
    Ok(Json(fetch_book(&state, &token_id).await?))
}

async fn midpoint(State(state): State<AppState>, Path(token_id): Path<String>) -> AppResult<Json<Value>> {
    let book = fetch_book(&state, &token_id).await?;
    let bid = book.bids.first().map(|b| b.price).unwrap_or(0.49);
    let ask = book.asks.first().map(|a| a.price).unwrap_or(0.51);
    Ok(Json(json!({ "midpoint": round4((bid + ask) / 2.0) })))
}

async fn best_bid_ask(State(state): State<AppState>, Path(token_id): Path<String>) -> AppResult<Json<Value>> {
    let book = fetch_book(&state, &token_id).await?;
    Ok(Json(json!({
        "bid": book.bids.first().map(|b| b.price),
        "ask": book.asks.first().map(|a| a.price)
    })))
}

// ── Client lifecycle ──────────────────────────────────────────────────────────

async fn client_open(
    State(state): State<AppState>,
    Json(body): Json<ClientOpenRequest>,
) -> AppResult<Json<ClientOpenResponse>> {
    let client_id = make_client_id(&body);

    if !state.clients.contains_key(&client_id) {
        let proxy = body.proxy_address.as_deref();
        let sig_type = body.signature_type.unwrap_or(0);
        // Per-request builder_code overrides global setting; fall back to settings
        let builder_code = body.builder_code.as_deref()
            .filter(|s| !s.is_empty())
            .or_else(|| state.settings.builder_code.as_deref());
        let handle = live::open_live_client(
            &body.privy_wallet_id,
            &body.wallet_address,
            body.chain_id as u64,
            &state.settings.clob_host,
            &state.settings.privy_app_id,
            &state.settings.privy_app_secret,
            &state.settings.privy_authorization_key,
            &state.settings.privy_authorization_key_id,
            proxy,
            sig_type,
            builder_code,
            state.http.clone(),
        )
        .await
        .map_err(|e| {
            let short = format!("{e}").chars().take(120).collect::<String>();
            tracing::error!("[open] {} — {}", &body.wallet_address[..body.wallet_address.len().min(10)], short);
            AppError::Internal(format!("live client init failed: {e:#}"))
        })?;

        state.clients.insert(client_id.clone(), Arc::new(handle));
    }

    Ok(Json(ClientOpenResponse { client_id, mode: "live".to_string() }))
}

async fn client_close(State(state): State<AppState>, Json(body): Json<ClientIdRequest>) -> AppResult<Json<Value>> {
    state.clients.remove(&body.client_id);
    Ok(Json(json!({ "ok": true })))
}

async fn client_usdc_balance(State(state): State<AppState>, Json(body): Json<ClientIdRequest>) -> AppResult<Json<Value>> {
    let bal = get_client(&state, &body.client_id)?
        .usdc_balance().await
        .map_err(|e| AppError::Internal(format!("usdc balance failed: {e}")))?;
    Ok(Json(json!({ "balance": bal })))
}

async fn client_token_balance(State(state): State<AppState>, Json(body): Json<TokenBalanceRequest>) -> AppResult<Json<Value>> {
    let bal = get_client(&state, &body.client_id)?
        .token_balance(&body.token_id).await
        .map_err(|e| AppError::Internal(format!("token balance failed: {e}")))?;
    Ok(Json(json!({ "balance": bal })))
}

async fn client_ensure_sell_allowance(State(state): State<AppState>, Json(body): Json<EnsureSellAllowanceRequest>) -> AppResult<Json<Value>> {
    get_client(&state, &body.client_id)?
        .update_balance_allowance(&body.token_id).await
        .map_err(|e| AppError::Internal(format!("allowance update failed: {e}")))?;
    Ok(Json(json!({ "ok": true })))
}

async fn client_place_limit_order(State(state): State<AppState>, Json(body): Json<PlaceLimitOrderRequest>) -> AppResult<Json<Value>> {
    let tick = body.tick_size.as_deref();
    let neg_risk = body.neg_risk.unwrap_or(false);
    let token_short = &body.token_id[..body.token_id.len().min(12)];
    let client_short = &body.client_id[..body.client_id.len().min(8)];
    let resp = get_client(&state, &body.client_id)?
        .place_limit_order(&body.token_id, body.price, body.size, &body.side, tick, neg_risk).await
        .map_err(|e| {
            let msg = format!("{e:#}");
            let msg_lc = msg.to_lowercase();
            let is_poly_rejection = ["not enough balance", "balance is not enough", "not enough allowance",
                "lower than minimum", "insufficient", "400", "bad request", "orderbook is closed",
                "maker address not allowed", "not allowed", "unauthorized maker"]
                .iter().any(|kw| msg_lc.contains(kw));
            // Extract first meaningful error fragment (skip chain prefixes)
            let short_err = msg.lines()
                .find(|l| l.contains('{') || l.contains("error") || l.contains("Error"))
                .unwrap_or(&msg)
                .chars().take(120).collect::<String>();
            if is_poly_rejection {
                // balance/allowance errors are noisy and handled by Python — log at warn, compact
                if msg_lc.contains("balance") || msg_lc.contains("allowance") {
                    tracing::warn!("[order] {} {} {:.4}@{:.4} — balance/allowance rejected", client_short, body.side, body.size, body.price);
                } else {
                    tracing::warn!("[order] {} {} {:.4}@{:.4} tok={} — {}", client_short, body.side, body.size, body.price, token_short, short_err);
                }
                AppError::BadRequest(format!("place_limit_order failed: {msg}"))
            } else {
                tracing::error!("[order] {} {} {:.4}@{:.4} tok={} — {}", client_short, body.side, body.size, body.price, token_short, short_err);
                AppError::Internal(format!("place_limit_order failed: {msg}"))
            }
        })?;
    Ok(Json(resp))
}

async fn client_open_orders(State(state): State<AppState>, Json(body): Json<ClientIdRequest>) -> AppResult<Json<Value>> {
    let orders = get_client(&state, &body.client_id)?
        .open_orders().await
        .map_err(|e| AppError::Internal(format!("open orders failed: {e}")))?;
    Ok(Json(orders))
}

async fn client_order_status(State(state): State<AppState>, Json(body): Json<OrderStatusRequest>) -> AppResult<Json<Value>> {
    let status = get_client(&state, &body.client_id)?
        .order_status(&body.order_id).await
        .map_err(|e| AppError::Internal(format!("order status failed: {e}")))?;
    Ok(Json(status))
}

async fn client_cancel_order(State(state): State<AppState>, Json(body): Json<CancelOrderRequest>) -> AppResult<Json<Value>> {
    let resp = get_client(&state, &body.client_id)?
        .cancel_order(&body.order_id).await
        .map_err(|e| AppError::Internal(format!("cancel order failed: {e}")))?;
    Ok(Json(resp))
}

// ── Wallet USDC balance (no auth required — reads on-chain via RPC) ───────────

async fn wallet_usdc_balance(
    State(state): State<AppState>,
    Path(address): Path<String>,
) -> AppResult<Json<Value>> {
    // pUSD on Polygon — V2 collateral token (ERC-20, 6 decimals)
    const RPCS: &[&str] = &[
        "https://polygon-bor-rpc.publicnode.com",
        "https://rpc.ankr.com/polygon",
        "https://1rpc.io/matic",
    ];

    // balanceOf(address) = selector 0x70a08231 + padded address
    let addr_clean = address.trim_start_matches("0x").to_lowercase();
    let padded = format!("{:0>64}", addr_clean);
    let data = format!("0x70a08231{}", padded);

    let payload = serde_json::json!({
        "jsonrpc": "2.0", "id": 1, "method": "eth_call",
        "params": [{"to": PUSD_POLYGON, "data": data}, "latest"]
    });

    for rpc in RPCS {
        match state.http.post(*rpc).json(&payload).send().await {
            Ok(resp) => {
                let status = resp.status();
                match resp.json::<Value>().await {
                    Ok(body) => {
                        if let Some(hex) = body.get("result").and_then(|v| v.as_str()) {
                            if hex != "0x" && !hex.is_empty() {
                                let raw = i128::from_str_radix(hex.trim_start_matches("0x"), 16)
                                    .unwrap_or(0);
                                let balance = raw as f64 / 1_000_000.0;
                                tracing::debug!("wallet_balance address={} raw_hex={} balance={}", address, hex, balance);
                                return Ok(Json(json!({ "balance": balance, "address": address })));
                            }
                        }
                    }
                    Err(e) => tracing::debug!("wallet_balance rpc={} decode error: {}", rpc, e),
                }
            }
            Err(e) => tracing::debug!("wallet_balance rpc={} request error: {}", rpc, e),
        }
    }
    tracing::warn!("wallet_balance all RPCs failed for address={}", address);
    Ok(Json(json!({ "balance": null, "address": address })))
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn get_client(state: &AppState, client_id: &str) -> AppResult<Arc<LiveClientHandle>> {
    state.clients.get(client_id)
        .map(|v| Arc::clone(v.value()))
        .ok_or_else(|| AppError::BadRequest("Unknown client_id".to_string()))
}

async fn fetch_book(state: &AppState, token_id: &str) -> AppResult<OrderBookResponse> {
    match state.http.get(format!("{}/book", state.settings.clob_host))
        .query(&[("token_id", token_id)])
        .send().await
    {
        Ok(resp) if resp.status().is_success() => {
            let value = resp.json::<Value>().await
                .map_err(|e| AppError::Internal(format!("book decode failed: {e}")))?;
            Ok(normalize_book(value))
        }
        Ok(resp) => {
            if resp.status().as_u16() == 404 {
                tracing::debug!("book 404 (market resolved): {token_id}");
            } else {
                error!("book request failed with status {}", resp.status());
            }
            Err(AppError::Internal(format!("book request failed: {}", resp.status())))
        }
        Err(err) => {
            error!("book request error: {err}");
            Err(AppError::Internal(format!("book request error: {err}")))
        }
    }
}

fn normalize_book(value: Value) -> OrderBookResponse {
    let mut bids = parse_levels(value.get("bids"));
    let mut asks = parse_levels(value.get("asks"));
    bids.sort_by(|a, b| b.price.partial_cmp(&a.price).unwrap());
    asks.sort_by(|a, b| a.price.partial_cmp(&b.price).unwrap());
    OrderBookResponse { bids, asks }
}

fn parse_levels(value: Option<&Value>) -> Vec<models::BookLevel> {
    value.and_then(Value::as_array).map(|entries| {
        entries.iter().filter_map(|entry| {
            if let Some(obj) = entry.as_object() {
                Some(models::BookLevel {
                    price: parse_num(obj.get("price")?)?,
                    size: parse_num(obj.get("size")?)?,
                })
            } else if let Some(arr) = entry.as_array() {
                let price = arr.first().and_then(parse_num)?;
                let size = arr.get(1).and_then(parse_num)?;
                Some(models::BookLevel { price, size })
            } else {
                None
            }
        }).collect()
    }).unwrap_or_default()
}

fn parse_num(value: &Value) -> Option<f64> {
    if let Some(n) = value.as_f64() { Some(n) } else { value.as_str().and_then(|s| s.parse().ok()) }
}

fn make_client_id(body: &ClientOpenRequest) -> String {
    let mut hasher = Sha256::new();
    hasher.update(body.privy_wallet_id.as_bytes());
    hasher.update(body.wallet_address.as_bytes());
    hasher.update(body.chain_id.to_le_bytes());
    let digest = hasher.finalize();
    format!("{}-{}", Uuid::new_v4(), hex_prefix(&digest[..8]))
}

fn hex_prefix(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn extract_slug(url: &str) -> AppResult<String> {
    let trimmed = url.trim().trim_end_matches('/');
    let clean = trimmed.split(['?', '#']).next()
        .ok_or_else(|| AppError::BadRequest("Invalid market URL".to_string()))?;
    let slug = clean.rsplit('/').next().filter(|s| !s.is_empty())
        .ok_or_else(|| AppError::BadRequest("Cannot extract slug".to_string()))?;
    Ok(slug.to_string())
}

fn parse_token_ids(market: &Value) -> Vec<String> {
    let Some(raw) = market.get("clobTokenIds") else { return Vec::new() };
    if let Some(arr) = raw.as_array() {
        return arr.iter().filter_map(|v| {
            v.as_str().map(ToString::to_string).or_else(|| v.as_i64().map(|n| n.to_string()))
        }).collect();
    }
    if let Some(s) = raw.as_str() {
        if let Ok(value) = serde_json::from_str::<Value>(s) {
            return value.as_array().map(|arr| arr.iter().filter_map(|v| {
                v.as_str().map(ToString::to_string).or_else(|| v.as_i64().map(|n| n.to_string()))
            }).collect()).unwrap_or_default();
        }
    }
    Vec::new()
}

fn round4(value: f64) -> f64 {
    (value * 10_000.0).round() / 10_000.0
}
