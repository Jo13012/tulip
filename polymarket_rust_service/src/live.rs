//! live.rs — Real Polymarket CLOB client backed by polymarket-client-sdk + Privy Server Wallets.

use alloy::signers::Signer as _;
use anyhow::{Context, Result};
use polymarket_client_sdk::clob::types::request::{
    BalanceAllowanceRequest, OrderBookSummaryRequest, OrdersRequest,
};
use polymarket_client_sdk::clob::types::{AssetType, Side, SignatureType};
use polymarket_client_sdk::clob::{Client, Config};
use polymarket_client_sdk::auth::state::Authenticated;
use polymarket_client_sdk::auth::Normal;
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

    let unauth = Client::new(clob_host, Config::default()).context("build clob client")?;
    let mut auth_builder = unauth
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
        .context("CLOB authentication failed")?;

    Ok(LiveClientHandle { client, signer, signature_type: sig_type })
}

impl LiveClientHandle {
    pub async fn usdc_balance(&self) -> Result<f64> {
        let req = BalanceAllowanceRequest::builder()
            .asset_type(AssetType::Collateral)
            .signature_type(self.signature_type)
            .build();
        let resp = self.client.balance_allowance(req).await.context("balance_allowance(USDC)")?;
        Ok(resp.balance.to_string().parse().unwrap_or(0.0))
    }

    pub async fn token_balance(&self, token_id: &str) -> Result<f64> {
        let req = BalanceAllowanceRequest::builder()
            .asset_type(AssetType::Conditional)
            .token_id(token_id.to_string())
            .signature_type(self.signature_type)
            .build();
        let resp = self.client.balance_allowance(req).await.context("balance_allowance(token)")?;
        Ok(resp.balance.to_string().parse().unwrap_or(0.0))
    }

    pub async fn update_balance_allowance(&self, token_id: &str) -> Result<()> {
        let req = BalanceAllowanceRequest::builder()
            .asset_type(AssetType::Conditional)
            .token_id(token_id.to_string())
            .signature_type(self.signature_type)
            .build();
        self.client.update_balance_allowance(req).await.context("update_balance_allowance")?;
        Ok(())
    }

    pub async fn place_limit_order(&self, token_id: &str, price: f64, size: f64, side: &str) -> Result<Value> {
        use polymarket_client_sdk::clob::types::OrderType;

        let side_enum = match side.to_uppercase().as_str() {
            "BUY" => Side::Buy,
            "SELL" => Side::Sell,
            other => anyhow::bail!("unknown side: {other}"),
        };

        let price_dec = Decimal::from_str(&format!("{price:.3}")).context("parse price")?;
        let size_dec = Decimal::from_str(&format!("{size:.2}")).context("parse size")?;

        let signable = self.client
            .limit_order()
            .token_id(token_id)
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
        let req = OrdersRequest::builder().build();
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
                "asset_id": order.asset_id,
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
            "asset_id": resp.asset_id,
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
        let req = OrderBookSummaryRequest::builder().token_id(token_id.to_string()).build();
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
