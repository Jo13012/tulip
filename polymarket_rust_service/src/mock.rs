use crate::models::{OrderBookResponse, OrderResponse};
use dashmap::DashMap;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use uuid::Uuid;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct MockOrder {
    pub order_id: String,
    pub token_id: String,
    pub side: String,
    pub price: f64,
    pub original_size: f64,
    pub size_matched: f64,
    pub status: String,
}

#[derive(Clone, Debug)]
pub struct MockClientState {
    pub usdc_balance: f64,
    pub token_balances: DashMap<String, f64>,
    pub sell_allowances: DashMap<String, bool>,
    pub orders: DashMap<String, MockOrder>,
}

impl MockClientState {
    pub fn new(initial_usdc: f64) -> Self {
        Self {
            usdc_balance: initial_usdc,
            token_balances: DashMap::new(),
            sell_allowances: DashMap::new(),
            orders: DashMap::new(),
        }
    }

    pub fn place_limit_order(
        &self,
        token_id: &str,
        side: &str,
        price: f64,
        size: f64,
        book: &OrderBookResponse,
    ) -> OrderResponse {
        let order_id = Uuid::new_v4().to_string();
        let mut matched = false;
        let mut taking_amount = None;
        let mut making_amount = None;
        let normalized_side = side.to_uppercase();

        if normalized_side == "BUY" {
            let best_ask = book.asks.first().map(|l| l.price).unwrap_or(1.0);
            if price >= best_ask {
                matched = true;
                let usdc_cost = size * best_ask;
                taking_amount = Some(size);
                making_amount = Some(usdc_cost);
                self.token_balances
                    .entry(token_id.to_string())
                    .and_modify(|v| *v += size)
                    .or_insert(size);
            }
        } else {
            let best_bid = book.bids.first().map(|l| l.price).unwrap_or(0.0);
            if price <= best_bid {
                matched = true;
                let token_amount = size;
                taking_amount = Some(token_amount);
                making_amount = Some(token_amount * best_bid);
                self.token_balances
                    .entry(token_id.to_string())
                    .and_modify(|v| *v = (*v - token_amount).max(0.0))
                    .or_insert(0.0);
            }
        }

        let status = if matched { "matched" } else { "live" }.to_string();
        if !matched {
            self.orders.insert(
                order_id.clone(),
                MockOrder {
                    order_id: order_id.clone(),
                    token_id: token_id.to_string(),
                    side: normalized_side.clone(),
                    price,
                    original_size: size,
                    size_matched: 0.0,
                    status: status.clone(),
                },
            );
        }

        OrderResponse {
            order_id,
            status,
            taking_amount,
            making_amount,
            price: Some(price),
            side: Some(normalized_side),
            size_matched: Some(if matched { size } else { 0.0 }),
            original_size: Some(size),
        }
    }

    pub fn open_orders_map(&self) -> HashMap<String, OrderResponse> {
        self.orders
            .iter()
            .map(|entry| {
                let order = entry.value();
                (
                    order.order_id.clone(),
                    OrderResponse {
                        order_id: order.order_id.clone(),
                        status: order.status.clone(),
                        taking_amount: None,
                        making_amount: None,
                        price: Some(order.price),
                        side: Some(order.side.clone()),
                        size_matched: Some(order.size_matched),
                        original_size: Some(order.original_size),
                    },
                )
            })
            .collect()
    }

    pub fn cancel_order(&self, order_id: &str) -> OrderResponse {
        if let Some((_, order)) = self.orders.remove(order_id) {
            return OrderResponse {
                order_id: order.order_id,
                status: "cancelled".to_string(),
                taking_amount: None,
                making_amount: None,
                price: Some(order.price),
                side: Some(order.side),
                size_matched: Some(order.size_matched),
                original_size: Some(order.original_size),
            };
        }

        OrderResponse {
            order_id: order_id.to_string(),
            status: "cancelled".to_string(),
            taking_amount: None,
            making_amount: None,
            price: None,
            side: None,
            size_matched: Some(0.0),
            original_size: Some(0.0),
        }
    }

    pub fn get_order_status(&self, order_id: &str) -> OrderResponse {
        if let Some(order) = self.orders.get(order_id) {
            return OrderResponse {
                order_id: order.order_id.clone(),
                status: order.status.clone(),
                taking_amount: None,
                making_amount: None,
                price: Some(order.price),
                side: Some(order.side.clone()),
                size_matched: Some(order.size_matched),
                original_size: Some(order.original_size),
            };
        }

        OrderResponse {
            order_id: order_id.to_string(),
            status: "cancelled".to_string(),
            taking_amount: None,
            making_amount: None,
            price: None,
            side: None,
            size_matched: Some(0.0),
            original_size: Some(0.0),
        }
    }
}
