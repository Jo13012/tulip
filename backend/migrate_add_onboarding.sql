-- Add Polymarket onboarding columns to wallets table
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS clob_api_key TEXT;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS clob_api_secret TEXT;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS clob_api_passphrase TEXT;
ALTER TABLE wallets ADD COLUMN IF NOT EXISTS onboarding_state VARCHAR(20) NOT NULL DEFAULT 'pending';

-- Mark existing wallet as needing onboarding retry
UPDATE wallets SET onboarding_state = 'failed' WHERE onboarding_state = 'pending';
