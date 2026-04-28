//! live.rs — Polymarket CLOB V2 client backed by polymarket_client_sdk_v2 + Privy Server Wallets.

use alloy::signers::Signer as _;
use anyhow::{Context, Result};
use polymarket_client_sdk_v2::clob::types::request::{
    BalanceAllowanceRequest, OrdersRequest,
};
use polymarket_client_sdk_v2::clob::types::{AssetType, OrderType, Side, SignatureType};
use polymarket_client_sdk_v2::clob::{Client, Config};
use polymarket_client_sdk_v2::auth::state::Authenticated;
use polymarket_client_sdk_v2::auth::Normal;
use polymarket_client_sdk_v2::types::B256;
use reqwest::Client as HttpClient;
use rust_decimal::Decimal;
use serde_json::{json, Value};
use std::str::FromStr as _;

use crate::privy_signer::PrivySigner;

pub struct LiveClientHandle {
    client: Client<Authenticated<Normal>>,
    signer: PrivySigner,
    signature_type: SignatureType,
}

pub async fn open_live_client(
    privy_wallet_id: &str,
    wallet_address: &str,
    chain_id: u64,
    clob_host: &str,
    privy_app_id: &str,
    privy_app_secret: &str,
    privy_authorization_key: &str,
    privy_authorization_key_id: &str,
    proxy_address: Option<&str>,
    signature_type_int: i32,
    builder_code: Option<&str>,
    http: HttpClient,
) -> Result<LiveClientHandle> {
    let mut signer = PrivySigner::new(
        privy_wallet_id.to_string(),
        wallet_address.to_string(),
        chain_id,
        privy_app_id.to_string(),
        privy_app_secret.to_string(),
        privy_authorization_key.to_string(),
        privy_authorization_key_id.to_string(),
        http,
    )?;
    signer.set_chain_id(Some(chain_id));

    let sig_type = match signature_type_int {
        2 => SignatureType::GnosisSafe,
        1 => SignatureType::Proxy,
        _ => SignatureType::Eoa,
    };

    let config = {
        let base = Config::builder().use_server_time(true);
        if let Some(code_str) = builder_code.filter(|s| !s.is_empty()) {
            match B256::from_str(code_str) {
                Ok(code) => base.builder_code(code).build(),
                Err(e) => {
                    tracing::warn!("Invalid builder_code '{code_str}': {e} — skipping");
                    base.build()
                }
            }
        } else {
            base.build()
        }
    };

    let mut auth_builder = Client::new(clob_host, config)
        .context("build clob v2 client")?
        .authentication_builder(&signer)
        .signature_type(sig_type);

    if let Some(proxy) = proxy_address {
        if !proxy.is_empty() {
            if let Ok(addr) = proxy.parse::<alloy::primitives::Address>() {
                auth_builder = auth_builder.funder(addr);
            }
        }
    }

    let client = auth_builder
        .authenticate()
        .await
        .context("CLOB V2 authentication failed")?;

    Ok(LiveClientHandle { client, signer, signature_type: sig_type })
}

impl LiveClientHandle {
    pub async fn usdc_balance(&self) -> Result<f64> {
        let req = BalanceAllowanceRequest::builder()
            .asset_type(AssetType::Collateral)
            .build();
        let resp = self.client.balance_allowance(req).await.context("balance_allowance(pUSD)")?;
        Ok(resp.balance.to_string().parse().unwrap_or(0.0))
    }

    pub async fn token_balance(&self, token_id: &str) -> Result<f64> {
        use polymarket_client_sdk_v2::types::U256;
        let tid = U256::from_str(token_id).context("parse token_id")?;
        let req = BalanceAllowanceRequest::builder()
            .asset_type(AssetType::Conditional)
            .token_id(tid)
            .build();
        let resp = self.client.balance_allowance(req).await.context("balance_allowance(token)")?;
        Ok(resp.balance.to_string().parse().unwrap_or(0.0))
    }

    pub async fn update_balance_allowance(&self, token_id: &str) -> Result<()> {
        use polymarket_client_sdk_v2::clob::types::request::UpdateBalanceAllowanceRequest;
        use polymarket_client_sdk_v2::types::U256;
        let tid = U256::from_str(token_id).context("parse token_id")?;
        let req = UpdateBalanceAllowanceRequest::builder()
            .asset_type(AssetType::Conditional)
            .token_id(tid)
            .build();
        self.client.update_balance_allowance(req).await.context("update_balance_allowance")?;
        Ok(())
    }

    pub async fn place_limit_order(
        &self,
        token_id: &str,
        price: f64,
        size: f64,
        side: &str,
        tick_size: Option<&str>,
        neg_risk: bool,
    ) -> Result<Value> {
        use polymarket_client_sdk_v2::types::U256;

        let _ = neg_risk; // V2 SDK infers neg_risk from the token's market info

        let side_enum = match side.to_uppercase().as_str() {
            "BUY" => Side::Buy,
            "SELL" => Side::Sell,
            other => anyhow::bail!("unknown side: {other}"),
        };

        let tid = U256::from_str(token_id).context("parse token_id")?;

        // Snap price to tick precision
        let sdk_tick = self.client.tick_size(tid).await
            .map(|r| r.minimum_tick_size.to_string())
            .unwrap_or_else(|_| tick_size.unwrap_or("0.001").to_string());

        let tick_dec = Decimal::from_str(&sdk_tick)
            .unwrap_or_else(|_| Decimal::from_str("0.001").unwrap());
        let price_raw = Decimal::from_str(&format!("{price:.6}")).context("parse price")?;
        let price_dec = (price_raw / tick_dec).trunc() * tick_dec;
        let price_dec = price_dec.max(tick_dec).min(Decimal::ONE - tick_dec);

        let size_dec = Decimal::from_str(&format!("{size:.2}")).context("parse size")?;

        let signable = self.client
            .limit_order()
            .token_id(tid)
            .side(side_enum)
            .price(price_dec)
            .size(size_dec)
            .order_type(OrderType::GTC)
            .build()
            .await
            .context("build limit order")?;

        let signed = self.client.sign(&self.signer, signable).await.context("sign order")?;
        let resp = self.client.post_order(signed).await.context("post_order")?;

        Ok(json!({
            "orderID": resp.order_id,
            "status": resp.status.to_string(),
            "success": resp.success,
            "makingAmount": resp.making_amount.to_string().parse::<f64>().unwrap_or(0.0),
            "takingAmount": resp.taking_amount.to_string().parse::<f64>().unwrap_or(0.0),
            "errorMsg": resp.error_msg,
        }))
    }

    pub async fn open_orders(&self) -> Result<Value> {
        use std::collections::HashMap;
        let req = OrdersRequest::default();
        let page = self.client.orders(&req, None).await.context("fetch orders")?;
        let mut map: HashMap<String, Value> = HashMap::new();
        for order in &page.data {
            map.insert(order.id.clone(), json!({
                "orderID": order.id,
                "status": order.status.to_string(),
                "price": order.price.to_string().parse::<f64>().unwrap_or(0.0),
                "side": order.side.to_string(),
                "original_size": order.original_size.to_string().parse::<f64>().unwrap_or(0.0),
                "size_matched": order.size_matched.to_string().parse::<f64>().unwrap_or(0.0),
                "asset_id": order.asset_id.to_string(),
            }));
        }
        Ok(serde_json::to_value(map)?)
    }

    pub async fn order_status(&self, order_id: &str) -> Result<Value> {
        let resp = self.client.order(order_id).await.context("fetch order status")?;
        Ok(json!({
            "orderID": resp.id,
            "status": resp.status.to_string(),
            "price": resp.price.to_string().parse::<f64>().unwrap_or(0.0),
            "side": resp.side.to_string(),
            "original_size": resp.original_size.to_string().parse::<f64>().unwrap_or(0.0),
            "size_matched": resp.size_matched.to_string().parse::<f64>().unwrap_or(0.0),
            "asset_id": resp.asset_id.to_string(),
        }))
    }

    pub async fn cancel_order(&self, order_id: &str) -> Result<Value> {
        let resp = self.client.cancel_order(order_id).await.context("cancel_order")?;
        Ok(json!({
            "orderID": order_id,
            "status": "cancelled",
            "canceled": resp.canceled,
            "not_canceled": resp.not_canceled,
        }))
    }

    #[allow(dead_code)]
    pub async fn order_book(&self, token_id: &str) -> Result<Value> {
        use polymarket_client_sdk_v2::clob::types::request::OrderBookSummaryRequest;
        use polymarket_client_sdk_v2::types::U256;
        let tid = U256::from_str(token_id).context("parse token_id")?;
        let req = OrderBookSummaryRequest::builder().token_id(tid).build();
        let resp = self.client.order_book(&req).await.context("order_book")?;
        let bids: Vec<Value> = resp.bids.iter().map(|l| json!({
            "price": l.price.to_string().parse::<f64>().unwrap_or(0.0),
            "size": l.size.to_string().parse::<f64>().unwrap_or(0.0),
        })).collect();
        let asks: Vec<Value> = resp.asks.iter().map(|l| json!({
            "price": l.price.to_string().parse::<f64>().unwrap_or(0.0),
            "size": l.size.to_string().parse::<f64>().unwrap_or(0.0),
        })).collect();
        Ok(json!({ "bids": bids, "asks": asks }))
    }
}
