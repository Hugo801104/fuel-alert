create extension if not exists pgcrypto;

create table if not exists subscriptions (
    id uuid primary key default gen_random_uuid(),
    latitude double precision not null,
    longitude double precision not null,
    radius_km double precision not null,
    fuel_type text not null,
    channel text not null default 'Telegram',
    telegram_chat_id text,
    discord_webhook_url text,
    duration_days integer not null default 10,
    sent_count integer not null default 0,
    active boolean not null default true,
    started_at timestamptz not null default now(),
    expires_at timestamptz not null,
    next_run_at timestamptz not null default now(),
    last_sent_at timestamptz,
    created_at timestamptz not null default now(),
    constraint subscriptions_radius_check check (radius_km > 0 and radius_km <= 50),
    constraint subscriptions_duration_check check (duration_days between 1 and 30),
    constraint subscriptions_sent_count_check check (sent_count >= 0)
);

alter table subscriptions add column if not exists channel text not null default 'Telegram';
alter table subscriptions add column if not exists telegram_chat_id text;
alter table subscriptions add column if not exists discord_webhook_url text;
alter table subscriptions alter column telegram_chat_id drop not null;

alter table subscriptions drop constraint if exists subscriptions_channel_data_check;
alter table subscriptions add constraint subscriptions_channel_data_check check (
    (channel = 'Telegram' and telegram_chat_id is not null and discord_webhook_url is null)
    or (channel = 'Discord' and discord_webhook_url is not null and telegram_chat_id is null)
);

create index if not exists subscriptions_due_idx
    on subscriptions (active, next_run_at);

create table if not exists notification_logs (
    id uuid primary key default gen_random_uuid(),
    subscription_id uuid not null references subscriptions(id) on delete cascade,
    notification_date date not null,
    sent_at timestamptz not null default now(),
    unique (subscription_id, notification_date)
);

create table if not exists subscription_errors (
    id uuid primary key default gen_random_uuid(),
    subscription_id uuid not null references subscriptions(id) on delete cascade,
    error_message text not null,
    occurred_at timestamptz not null default now()
);

alter table subscriptions enable row level security;
alter table notification_logs enable row level security;
alter table subscription_errors enable row level security;

-- L'application utilise uniquement la clé service_role côté serveur.
-- Aucune donnée d'abonnement n'est exposée directement au navigateur.