//! privy_signer.rs — alloy Signer backed by Privy Server Wallet API.
//!
//! Private keys NEVER leave Privy's secure enclave.
//! Every sign call POSTs to `POST /v1/wallets/{id}/rpc`.

use alloy::primitives::{Address, ChainId, B256};
use alloy::signers::{Error as SignerError, Signer, SignerSync};
use alloy::primitives::Signature;
use anyhow::Context;
use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use reqwest::Client;
use serde_json::{json, Value};
use std::str::FromStr;

use crate::privy_auth;

const PRIVY_API_BASE: &str = "https://api.privy.io/v1";

/// A remote signer that delegates all signing to Privy Server Wallet API.
/// Implements `alloy::signers::Signer` so it can be plugged into polymarket-client-sdk.
pub struct PrivySigner {
    pub privy_wallet_id: String,
    pub wallet_address: Address,
    pub chain_id: Option<ChainId>,
    privy_app_id: String,
    privy_app_secret: String,
    privy_authorization_key: String,
    privy_authorization_key_id: String,
    http: Client,
}

impl std::fmt::Debug for PrivySigner {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("PrivySigner")
            .field("privy_wallet_id", &self.privy_wallet_id)
            .field("wallet_address", &self.wallet_address)
            .finish()
    }
}

impl PrivySigner {
    pub fn new(
        privy_wallet_id: String,
        wallet_address: String,
        chain_id: u64,
        privy_app_id: String,
        privy_app_secret: String,
        privy_authorization_key: String,
        privy_authorization_key_id: String,
        http: Client,
    ) -> anyhow::Result<Self> {
        let addr = Address::from_str(&wallet_address).context("invalid wallet_address")?;
        Ok(Self {
            privy_wallet_id,
            wallet_address: addr,
            chain_id: Some(chain_id),
            privy_app_id,
            privy_app_secret,
            privy_authorization_key,
            privy_authorization_key_id,
            http,
        })
    }

    fn basic_auth_header(&self) -> String {
        let creds = format!("{}:{}", self.privy_app_id, self.privy_app_secret);
        let encoded = B64.encode(creds.as_bytes());
        format!("Basic {encoded}")
    }

    /// Sign EIP-712 typed data. Returns hex signature (0x...).
    #[allow(dead_code)]
    pub async fn sign_typed_data_raw(&self, typed_data: Value) -> anyhow::Result<String> {
        let url = format!("{PRIVY_API_BASE}/wallets/{}/rpc", self.privy_wallet_id);
        let body = json!({
            "method": "eth_signTypedData_v4",
            "params": { "typed_data": typed_data }
        });

        let auth_headers = privy_auth::build_auth_headers(
            "POST", &url, &body,
            &self.privy_app_id,
            &self.privy_authorization_key,
            Some(&self.privy_authorization_key_id),
        )?;

        let mut req = self.http
            .post(&url)
            .header("Authorization", self.basic_auth_header())
            .header("privy-app-id", &self.privy_app_id)
            .header("Content-Type", "application/json");
        for (k, v) in &auth_headers {
            req = req.header(k.as_str(), v.as_str());
        }
        let resp = req.json(&body).send().await
            .context("Privy sign_typed_data HTTP call failed")?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            anyhow::bail!("Privy eth_signTypedData_v4 error {status}: {body}");
        }

        let payload: Value = resp.json().await.context("Privy response decode")?;
        let sig = payload["data"]["signature"]
            .as_str()
            .context("missing signature in Privy response")?
            .to_string();
        Ok(sig)
    }

    /// Sign a raw 32-byte hash via secp256k1_sign (no Ethereum prefix — matches Python secp256k1_sign).
    pub async fn secp256k1_sign_raw(&self, hash_hex: &str) -> anyhow::Result<String> {
        let url = format!("{PRIVY_API_BASE}/wallets/{}/rpc", self.privy_wallet_id);
        let body = json!({
            "method": "secp256k1_sign",
            "params": { "hash": hash_hex }
        });
        let auth_headers = privy_auth::build_auth_headers(
            "POST", &url, &body,
            &self.privy_app_id,
            &self.privy_authorization_key,
            Some(&self.privy_authorization_key_id),
        )?;
        let mut req = self.http
            .post(&url)
            .header("Authorization", self.basic_auth_header())
            .header("privy-app-id", &self.privy_app_id)
            .header("Content-Type", "application/json");
        for (k, v) in &auth_headers {
            req = req.header(k.as_str(), v.as_str());
        }
        let resp = req.json(&body).send().await
            .context("Privy secp256k1_sign HTTP call failed")?;
        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            anyhow::bail!("Privy secp256k1_sign error {status}: {body}");
        }
        let payload: Value = resp.json().await.context("Privy response decode")?;
        let sig = payload["data"]["signature"]
            .as_str()
            .context("missing signature in Privy secp256k1_sign response")?
            .to_string();
        Ok(sig)
    }

    /// Sign a personal message (utf-8). Returns hex signature (0x...).
    pub async fn personal_sign_raw(&self, message: &str) -> anyhow::Result<String> {
        let url = format!("{PRIVY_API_BASE}/wallets/{}/rpc", self.privy_wallet_id);
        let body = json!({
            "method": "personal_sign",
            "params": { "message": message, "encoding": "utf-8" }
        });

        let auth_headers = privy_auth::build_auth_headers(
            "POST", &url, &body,
            &self.privy_app_id,
            &self.privy_authorization_key,
            Some(&self.privy_authorization_key_id),
        )?;

        let mut req = self.http
            .post(&url)
            .header("Authorization", self.basic_auth_header())
            .header("privy-app-id", &self.privy_app_id)
            .header("Content-Type", "application/json");
        for (k, v) in &auth_headers {
            req = req.header(k.as_str(), v.as_str());
        }
        let resp = req.json(&body).send().await
            .context("Privy personal_sign HTTP call failed")?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            anyhow::bail!("Privy personal_sign error {status}: {body}");
        }

        let payload: Value = resp.json().await.context("Privy response decode")?;
        let sig = payload["data"]["signature"]
            .as_str()
            .context("missing signature in Privy response")?
            .to_string();
        Ok(sig)
    }
}

// ── alloy Signer implementation ───────────────────────────────────────────────

#[cfg_attr(not(target_family = "wasm"), async_trait::async_trait)]
impl Signer for PrivySigner {
    async fn sign_hash(&self, hash: &B256) -> Result<Signature, SignerError> {
        let hex = format!("0x{}", hex::encode(hash.as_slice()));
        let sig_hex = self
            .secp256k1_sign_raw(&hex)
            .await
            .map_err(|e| SignerError::Other(e.into()))?;
        let sig = Signature::from_str(&sig_hex)
            .map_err(|e| SignerError::Other(e.into()))?;
        Ok(sig)
    }

    async fn sign_message(&self, message: &[u8]) -> Result<Signature, SignerError> {
        let text = std::str::from_utf8(message)
            .map_err(|e| SignerError::Other(Box::new(e)))?;
        let sig_hex = self
            .personal_sign_raw(text)
            .await
            .map_err(|e| SignerError::Other(e.into()))?;
        let sig = Signature::from_str(&sig_hex)
            .map_err(|e| SignerError::Other(e.into()))?;
        Ok(sig)
    }

    fn address(&self) -> Address {
        self.wallet_address
    }

    fn chain_id(&self) -> Option<ChainId> {
        self.chain_id
    }

    fn set_chain_id(&mut self, chain_id: Option<ChainId>) {
        self.chain_id = chain_id;
    }
}

impl SignerSync for PrivySigner {
    fn sign_hash_sync(&self, _hash: &B256) -> Result<Signature, SignerError> {
        panic!("PrivySigner is async-only; use sign_hash instead")
    }

    fn chain_id_sync(&self) -> Option<ChainId> {
        self.chain_id
    }
}

/// Wraps PrivySigner but reports the Safe/proxy address as its own address.
/// This makes the SDK bind the CLOB API key to the Safe (POLY_ADDRESS=Safe in L1 headers)
/// while actual signing still goes through the EOA via Privy.
pub struct SafeSignerWrapper {
    inner: PrivySigner,
    safe_address: Address,
}

impl SafeSignerWrapper {
    pub fn new(inner: PrivySigner, safe_address: Address) -> Self {
        Self { inner, safe_address }
    }
}

#[cfg_attr(not(target_family = "wasm"), async_trait::async_trait)]
impl Signer for SafeSignerWrapper {
    async fn sign_hash(&self, hash: &B256) -> Result<Signature, SignerError> {
        self.inner.sign_hash(hash).await
    }

    async fn sign_message(&self, message: &[u8]) -> Result<Signature, SignerError> {
        self.inner.sign_message(message).await
    }

    fn address(&self) -> Address {
        self.safe_address
    }

    fn chain_id(&self) -> Option<ChainId> {
        self.inner.chain_id()
    }

    fn set_chain_id(&mut self, chain_id: Option<ChainId>) {
        self.inner.set_chain_id(chain_id);
    }
}

impl SignerSync for SafeSignerWrapper {
    fn sign_hash_sync(&self, _hash: &B256) -> Result<Signature, SignerError> {
        panic!("SafeSignerWrapper is async-only")
    }

    fn chain_id_sync(&self) -> Option<ChainId> {
        self.inner.chain_id()
    }
}
