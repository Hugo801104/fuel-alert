create extension if not exists pgcrypto;

create table if not exists subscriptions (
    id uuid primary key default gen_random_uuid(),
    latitude double precision not null,
    longitude double precision not null,
    radius_km double precision not null,
    fuel_type text not null,
    telegram_chat_id text not null,
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

create index if not exists subscriptions_due_idx
    on subscriptions (active, next_run_at);

create table if not exists notification_logs (
    id uuid primary key default gen_random_uuid(),
    subscription_id uuid not null references subscriptions(id) on delete cascade,
    notification_date date not null,
    sent_at timestamptz not null default now(),
    unique (subscription_id, notification_date)
);

alter table subscriptions enable row level security;
alter table notification_logs enable row level security;

-- L'application utilise uniquement la clé service_role côté serveur.
-- Aucune donnée d'abonnement n'est exposée directement au navigateur.