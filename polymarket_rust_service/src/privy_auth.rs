//! privy_auth.rs — Privy authorization-signature header builder.
//!
//! Mirrors Python's privy_wallet_service._sign_authorization + _authorization_headers exactly:
//!   - Extract raw P-256 scalar from PKCS8 DER (find [0x04, 0x20] marker)
//!   - JCS canonicalize payload (RFC 8785)
//!   - ECDSA P-256 SHA-256 sign → DER → base64-standard
//!   - Return headers: privy-authorization-signature, privy-request-expiry, privy-authorization-key-id

use anyhow::{Context, Result, bail};
use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use p256::ecdsa::{SigningKey, signature::Signer, DerSignature};
use serde_json::Value;
use std::time::{SystemTime, UNIX_EPOCH};

/// Extract raw 32-byte P-256 scalar from Privy authorization key.
/// Key format: "wallet-auth:<base64_pkcs8_der>"
/// Logic: find [0x04, 0x20] marker → next 32 bytes are the scalar.
fn extract_p256_scalar(key: &str) -> Result<[u8; 32]> {
    let raw = key.trim();
    let raw = raw.strip_prefix("wallet-auth:").unwrap_or(raw);
    let der = B64.decode(raw).context("base64 decode privy auth key")?;
    let marker = [0x04u8, 0x20u8];
    let idx = der
        .windows(2)
        .position(|w| w == marker)
        .context("privy auth key: marker [0x04,0x20] not found")?;
    let scalar = &der[idx + 2..idx + 34];
    let mut out = [0u8; 32];
    out.copy_from_slice(scalar);
    Ok(out)
}

/// RFC 8785 / JCS canonicalization — matches Python's _jcs_canonical_str.
fn jcs_canonical(v: &Value) -> String {
    match v {
        Value::Null => "null".into(),
        Value::Bool(b) => if *b { "true" } else { "false" }.into(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => serde_json::to_string(s).unwrap_or_default(),
        Value::Array(arr) => {
            let inner: Vec<String> = arr.iter().map(jcs_canonical).collect();
            format!("[{}]", inner.join(","))
        }
        Value::Object(map) => {
            let mut pairs: Vec<(&String, &Value)> = map.iter().collect();
            pairs.sort_by_key(|(k, _)| *k);
            let inner: Vec<String> = pairs
                .iter()
                .map(|(k, v)| {
                    format!(
                        "{}:{}",
                        serde_json::to_string(k).unwrap_or_default(),
                        jcs_canonical(v)
                    )
                })
                .collect();
            format!("{{{}}}", inner.join(","))
        }
    }
}

fn request_expiry_ms() -> u64 {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;
    now + 300_000 // +300s buffer, same as Python
}

/// Build Privy authorization headers for a signing request.
///
/// # Arguments
/// * `method` - HTTP method ("POST")
/// * `url` - full URL of the Privy API endpoint
/// * `body` - parsed JSON body that will be sent
/// * `app_id` - PRIVY_APP_ID
/// * `auth_key` - PRIVY_AUTHORIZATION_KEY ("wallet-auth:...")
/// * `auth_key_id` - PRIVY_AUTHORIZATION_KEY_ID (optional)
pub fn build_auth_headers(
    method: &str,
    url: &str,
    body: &Value,
    app_id: &str,
    auth_key: &str,
    auth_key_id: Option<&str>,
) -> Result<Vec<(String, String)>> {
    let expiry = request_expiry_ms();
    let expiry_str = expiry.to_string();

    // Build payload object — exact structure from Python / Privy SDK
    let payload = serde_json::json!({
        "version": 1,
        "method": method.to_uppercase(),
        "url": url.trim_end_matches('/'),
        "body": body,
        "headers": {
            "privy-app-id": app_id,
            "privy-request-expiry": expiry_str,
        }
    });

    let canonical = jcs_canonical(&payload);
    let canonical_bytes = canonical.as_bytes();

    // Extract scalar + sign
    let scalar = extract_p256_scalar(auth_key)?;
    let signing_key = SigningKey::from_bytes(scalar.as_slice().into())
        .context("build P-256 signing key")?;
    let sig: DerSignature = signing_key.sign(canonical_bytes);
    let sig_b64 = B64.encode(sig.as_bytes());

    let mut headers = vec![
        ("privy-authorization-signature".to_string(), sig_b64),
        ("privy-request-expiry".to_string(), expiry_str),
    ];
    if let Some(kid) = auth_key_id {
        if !kid.is_empty() {
            headers.push(("privy-authorization-key-id".to_string(), kid.to_string()));
        }
    }
    Ok(headers)
}
