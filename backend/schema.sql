-- ============================================================================
-- RatePath (ريت باث) — Supabase PostgreSQL + pgvector Market Memory Schema
-- Gate 1: Historical Macro Twins & 24-Hour Self-Evaluation Accuracy Lessons
-- Encoding: UTF-8
-- ============================================================================

-- 1. Required Extensions
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Core Market Memory Table
CREATE TABLE IF NOT EXISTS public.market_memory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_key TEXT UNIQUE NOT NULL,
    record_type TEXT NOT NULL CHECK (
        record_type IN ('historical_macro_twin', 'live_prediction', 'evaluated_lesson', 'calibration_lesson')
    ),
    event_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    macro_regime TEXT NOT NULL DEFAULT 'global_macro',

    -- Verified Provenance
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL CHECK (source_url ~ '^https?://'),

    -- Bilingual Macro Event & Strategist Thesis (UTF-8 EN / AR)
    headline_en TEXT NOT NULL,
    headline_ar TEXT NOT NULL,
    summary_en TEXT NOT NULL,
    summary_ar TEXT NOT NULL,

    -- Priced-In Risk & 24h Asset Directional Biases
    priced_in_warning JSONB NOT NULL DEFAULT '{}'::jsonb,
    assets_bias_24h JSONB NOT NULL,

    -- T+0 Baseline Snapshot & T+24h Realized Market Prices (Exact Decimal Strings)
    baseline_prices JSONB NOT NULL DEFAULT '{}'::jsonb,
    realized_prices_24h JSONB DEFAULT NULL,
    realized_changes_pct JSONB DEFAULT NULL,

    -- 24h Self-Evaluation Grading & Extracted Bilingual Lessons
    evaluation_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        evaluation_status IN ('pending', 'graded', 'historical_anchor')
    ),
    accuracy_score NUMERIC(5, 2) CHECK (
        accuracy_score IS NULL OR (accuracy_score >= 0.00 AND accuracy_score <= 100.00)
    ),
    evaluated_at TIMESTAMPTZ DEFAULT NULL,
    lesson_en TEXT DEFAULT NULL,
    lesson_ar TEXT DEFAULT NULL,

    -- Semantic Vector Embedding (768-d compatible with gemini-embedding-001)
    embedding vector(768),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.market_memory IS
'RatePath (ريت باث) institutional memory storing historical macro twins and 24-hour self-graded forecast lessons.';

-- 3. Automatic updated_at Trigger
CREATE OR REPLACE FUNCTION public.set_market_memory_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_market_memory_updated_at ON public.market_memory;
CREATE TRIGGER trg_market_memory_updated_at
BEFORE UPDATE ON public.market_memory
FOR EACH ROW
EXECUTE FUNCTION public.set_market_memory_updated_at();

-- 4. Performance & Vector Similarity Indexes
CREATE INDEX IF NOT EXISTS idx_market_memory_eval_due
    ON public.market_memory (evaluation_status, event_timestamp)
    WHERE evaluation_status = 'pending';

CREATE INDEX IF NOT EXISTS idx_market_memory_record_type
    ON public.market_memory (record_type, event_timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_market_memory_assets_bias_gin
    ON public.market_memory USING GIN (assets_bias_24h);

CREATE INDEX IF NOT EXISTS idx_market_memory_embedding_hnsw
    ON public.market_memory USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 5. Semantic Retrieval RPC: Historical Macro Twins + Graded Accuracy Lessons
CREATE OR REPLACE FUNCTION public.match_market_memory(
    query_embedding vector(768),
    match_threshold DOUBLE PRECISION DEFAULT 0.65,
    match_count INTEGER DEFAULT 5
)
RETURNS TABLE (
    id UUID,
    event_key TEXT,
    record_type TEXT,
    event_timestamp TIMESTAMPTZ,
    macro_regime TEXT,
    headline_en TEXT,
    headline_ar TEXT,
    assets_bias_24h JSONB,
    realized_changes_pct JSONB,
    accuracy_score NUMERIC(5, 2),
    lesson_en TEXT,
    lesson_ar TEXT,
    similarity DOUBLE PRECISION
)
LANGUAGE sql
STABLE
AS $$
    SELECT
        mm.id,
        mm.event_key,
        mm.record_type,
        mm.event_timestamp,
        mm.macro_regime,
        mm.headline_en,
        mm.headline_ar,
        mm.assets_bias_24h,
        mm.realized_changes_pct,
        mm.accuracy_score,
        mm.lesson_en,
        mm.lesson_ar,
        1 - (mm.embedding <=> query_embedding) AS similarity
    FROM public.market_memory AS mm
    WHERE mm.embedding IS NOT NULL
      AND mm.evaluation_status IN ('historical_anchor', 'graded')
      AND (1 - (mm.embedding <=> query_embedding)) >= match_threshold
    ORDER BY mm.embedding <=> query_embedding ASC
    LIMIT match_count;
$$;

-- 5b. Idempotent Migration Guard for Existing Deployments (adds `calibration_lesson`)
DO $$
BEGIN
    ALTER TABLE public.market_memory DROP CONSTRAINT IF EXISTS market_memory_record_type_check;
    ALTER TABLE public.market_memory ADD CONSTRAINT market_memory_record_type_check
        CHECK (record_type IN ('historical_macro_twin', 'live_prediction', 'evaluated_lesson', 'calibration_lesson'));
EXCEPTION WHEN undefined_table THEN
    NULL;
END $$;

-- 6. 24-Hour Due Predictions View for Evaluator Loop
CREATE OR REPLACE VIEW public.v_pending_24h_evaluations AS
SELECT
    id,
    event_key,
    event_timestamp,
    headline_en,
    headline_ar,
    assets_bias_24h,
    baseline_prices
FROM public.market_memory
WHERE record_type = 'live_prediction'
  AND evaluation_status = 'pending'
  AND event_timestamp <= (NOW() - INTERVAL '24 hours')
ORDER BY event_timestamp ASC;

-- 7. Row-Level Security (RLS) Hardening
ALTER TABLE public.market_memory ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow public read of verified lessons and twins" ON public.market_memory;
CREATE POLICY "Allow public read of verified lessons and twins"
    ON public.market_memory
    FOR SELECT
    TO anon, authenticated
    USING (evaluation_status IN ('historical_anchor', 'graded'));

DROP POLICY IF EXISTS "Allow service_role full access to market_memory" ON public.market_memory;
CREATE POLICY "Allow service_role full access to market_memory"
    ON public.market_memory
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- 8. Personal Workspace Preferences Table (`public.user_preferences`)
CREATE TABLE IF NOT EXISTS public.user_preferences (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT DEFAULT NULL,
    layout_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.user_preferences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can read own workspace preferences" ON public.user_preferences;
CREATE POLICY "Users can read own workspace preferences"
    ON public.user_preferences
    FOR SELECT
    TO authenticated
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can insert own workspace preferences" ON public.user_preferences;
CREATE POLICY "Users can insert own workspace preferences"
    ON public.user_preferences
    FOR INSERT
    TO authenticated
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users can update own workspace preferences" ON public.user_preferences;
CREATE POLICY "Users can update own workspace preferences"
    ON public.user_preferences
    FOR UPDATE
    TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- 9. Automated VIP Crypto Subscriptions Table (`public.vip_subscriptions`)
CREATE TABLE IF NOT EXISTS public.vip_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id TEXT UNIQUE NOT NULL,
    payment_id TEXT,
    user_email TEXT,
    telegram_user_id TEXT,
    plan_tier TEXT NOT NULL DEFAULT 'monthly',
    lang TEXT NOT NULL DEFAULT 'ar',
    amount_usd NUMERIC(10, 2) NOT NULL,
    payment_status TEXT NOT NULL DEFAULT 'waiting',
    invite_link_issued TEXT,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_vip_subscriptions_user_email
    ON public.vip_subscriptions (user_email);

CREATE INDEX IF NOT EXISTS idx_vip_subscriptions_status_expires
    ON public.vip_subscriptions (payment_status, expires_at);

DROP TRIGGER IF EXISTS trg_vip_subscriptions_updated_at ON public.vip_subscriptions;
CREATE TRIGGER trg_vip_subscriptions_updated_at
BEFORE UPDATE ON public.vip_subscriptions
FOR EACH ROW
EXECUTE FUNCTION public.set_market_memory_updated_at();

ALTER TABLE public.vip_subscriptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can view own subscription by email" ON public.vip_subscriptions;
CREATE POLICY "Users can view own subscription by email"
    ON public.vip_subscriptions
    FOR SELECT
    TO authenticated
    USING (auth.jwt() ->> 'email' = user_email);

DROP POLICY IF EXISTS "Allow service_role full access to vip_subscriptions" ON public.vip_subscriptions;
CREATE POLICY "Allow service_role full access to vip_subscriptions"
    ON public.vip_subscriptions
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- 10. Personal AI Tracker Bot Profiles & Custom Watchlist (`public.user_tracker_profiles`)
CREATE TABLE IF NOT EXISTS public.user_tracker_profiles (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT DEFAULT NULL,
    telegram_chat_id TEXT DEFAULT NULL,
    link_token TEXT UNIQUE DEFAULT NULL,
    lang TEXT NOT NULL DEFAULT 'ar' CHECK (lang IN ('en', 'ar')),
    plan_tier TEXT NOT NULL DEFAULT 'free' CHECK (
        plan_tier IN ('free', 'monthly_business_vip', 'monthly_personal_bot')
    ),
    watchlist JSONB NOT NULL DEFAULT '[]'::jsonb,
    daily_digest_enabled BOOLEAN NOT NULL DEFAULT true,
    digest_hour_utc SMALLINT NOT NULL DEFAULT 7 CHECK (digest_hour_utc >= 0 AND digest_hour_utc <= 23),
    last_digest_date DATE DEFAULT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_tracker_telegram_chat
    ON public.user_tracker_profiles (telegram_chat_id)
    WHERE telegram_chat_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_user_tracker_email
    ON public.user_tracker_profiles (email)
    WHERE email IS NOT NULL;

DROP TRIGGER IF EXISTS trg_user_tracker_profiles_updated_at ON public.user_tracker_profiles;
CREATE TRIGGER trg_user_tracker_profiles_updated_at
BEFORE UPDATE ON public.user_tracker_profiles
FOR EACH ROW
EXECUTE FUNCTION public.set_market_memory_updated_at();

ALTER TABLE public.user_tracker_profiles ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users can read own tracker profile" ON public.user_tracker_profiles;
CREATE POLICY "Users can read own tracker profile"
    ON public.user_tracker_profiles
    FOR SELECT
    TO authenticated
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Allow service_role full access to user_tracker_profiles" ON public.user_tracker_profiles;
CREATE POLICY "Allow service_role full access to user_tracker_profiles"
    ON public.user_tracker_profiles
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
