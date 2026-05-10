use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ResolveMarketResponse {
    pub question: String,
    pub condition_id: String,
    pub token_id_yes: String,
    pub token_id_no: String,
    pub tick_size: String,
    pub neg_risk: bool,
    pub active: bool,
    pub closed: bool,
    pub image: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct BookLevel {
    pub price: f64,
    pub size: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct OrderBookResponse {
    pub bids: Vec<BookLevel>,
    pub asks: Vec<BookLevel>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ClientOpenRequest {
    pub privy_wallet_id: String,
    pub wallet_address: String,
    pub chain_id: i32,
    pub proxy_address: Option<String>,
    pub signature_type: Option<i32>,
    /// V2: bytes32 hex string, e.g. "0xabc...". Overrides Settings.builder_code if provided.
    pub builder_code: Option<String>,
    /// Human-readable label for logs (display_name or username)
    pub display_name: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ClientOpenResponse {
    pub client_id: String,
    pub mode: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ClientIdRequest {
    pub client_id: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TokenBalanceRequest {
    pub client_id: String,
    pub token_id: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct EnsureSellAllowanceRequest {
    pub client_id: String,
    pub token_id: String,
    pub force: Option<bool>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PlaceLimitOrderRequest {
    pub client_id: String,
    pub token_id: String,
    pub price: f64,
    pub size: f64,
    pub side: String,
    pub tick_size: Option<String>,
    pub neg_risk: Option<bool>,
    /// Human-readable slot label for logs
    pub slot_label: Option<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct OrderStatusRequest {
    pub client_id: String,
    pub order_id: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CancelOrderRequest {
    pub client_id: String,
    pub order_id: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct OrderResponse {
    #[serde(rename = "orderID")]
    pub order_id: String,
    pub status: String,
    #[serde(rename = "takingAmount", skip_serializing_if = "Option::is_none")]
    pub taking_amount: Option<f64>,
    #[serde(rename = "makingAmount", skip_serializing_if = "Option::is_none")]
    pub making_amount: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub price: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub side: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub size_matched: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub original_size: Option<f64>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct OpenOrdersResponse {
    pub orders: HashMap<String, OrderResponse>,
}
