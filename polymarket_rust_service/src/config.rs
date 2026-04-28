use std::env;

#[derive(Clone, Debug)]
pub struct Settings {
    pub host: String,
    pub port: u16,
    pub clob_host: String,
    pub gamma_host: String,
    pub privy_app_id: String,
    pub privy_app_secret: String,
    pub privy_authorization_key: String,
    pub privy_authorization_key_id: String,
    /// V2: bytes32 builder code from polymarket.com/settings?tab=builder
    pub builder_code: Option<String>,
}

impl Settings {
    pub fn from_env() -> Self {
        Self {
            host: env::var("POLY_RUST_HOST").unwrap_or_else(|_| "127.0.0.1".to_string()),
            port: env::var("POLY_RUST_PORT")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(8020),
            clob_host: env::var("CLOB_HOST")
                .unwrap_or_else(|_| "https://clob-v2.polymarket.com".to_string()),
            gamma_host: env::var("GAMMA_HOST")
                .unwrap_or_else(|_| "https://gamma-api.polymarket.com".to_string()),
            privy_app_id: env::var("PRIVY_APP_ID").unwrap_or_default(),
            privy_app_secret: env::var("PRIVY_APP_SECRET").unwrap_or_default(),
            privy_authorization_key: env::var("PRIVY_AUTHORIZATION_KEY").unwrap_or_default(),
            privy_authorization_key_id: env::var("PRIVY_AUTHORIZATION_KEY_ID").unwrap_or_default(),
            builder_code: env::var("POLYMARKET_BUILDER_CODE").ok().filter(|s| !s.is_empty()),
        }
    }
}
