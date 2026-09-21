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
    constraint subscriptions_coordinates_check check (
        latitude between -90 and 90 and longitude between -180 and 180
    ),
    constraint subscriptions_fuel_type_check check (
        fuel_type in ('E10', 'SP95', 'SP98', 'Gazole', 'E85', 'GPLc')
    ),
    constraint subscriptions_channel_size_check check (
        char_length(channel) between 1 and 20
        and char_length(coalesce(telegram_chat_id, '')) <= 30
        and char_length(coalesce(discord_webhook_url, '')) <= 300
    ),
    constraint subscriptions_duration_check check (duration_days between 1 and 30),
    constraint subscriptions_sent_count_check check (sent_count >= 0)
);

alter table subscriptions add column if not exists channel text not null default 'Telegram';
alter table subscriptions add column if not exists telegram_chat_id text;
alter table subscriptions add column if not exists discord_webhook_url text;
alter table subscriptions alter column telegram_chat_id drop not null;

alter table subscriptions drop constraint if exists subscriptions_coordinates_check;
alter table subscriptions add constraint subscriptions_coordinates_check check (
    latitude between -90 and 90 and longitude between -180 and 180
);
alter table subscriptions drop constraint if exists subscriptions_fuel_type_check;
alter table subscriptions add constraint subscriptions_fuel_type_check check (
    fuel_type in ('E10', 'SP95', 'SP98', 'Gazole', 'E85', 'GPLc')
);
alter table subscriptions drop constraint if exists subscriptions_channel_size_check;
alter table subscriptions add constraint subscriptions_channel_size_check check (
    char_length(channel) between 1 and 20
    and char_length(coalesce(telegram_chat_id, '')) <= 30
    and char_length(coalesce(discord_webhook_url, '')) <= 300
);

alter table subscriptions drop constraint if exists subscriptions_channel_data_check;
alter table subscriptions add constraint subscriptions_channel_data_check check (
    (channel = 'Telegram' and telegram_chat_id is not null and discord_webhook_url is null)
    or (channel = 'Discord' and discord_webhook_url is not null and telegram_chat_id is null)
);

create index if not exists subscriptions_due_idx
    on subscriptions (active, next_run_at);

create unique index if not exists subscriptions_active_telegram_idx
    on subscriptions (telegram_chat_id)
    where active and channel = 'Telegram';

create unique index if not exists subscriptions_active_discord_idx
    on subscriptions (discord_webhook_url)
    where active and channel = 'Discord';

create or replace function enforce_active_subscription_limit()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if new.active and (
        tg_op = 'INSERT'
        or (tg_op = 'UPDATE' and not old.active)
    ) then
        perform pg_advisory_xact_lock(874321);
    end if;
    if new.active and (
        tg_op = 'INSERT'
        or (tg_op = 'UPDATE' and not old.active)
    ) and (select count(*) from subscriptions where active) >= 100 then
        raise exception 'active subscription limit reached';
    end if;
    return new;
end;
$$;

drop trigger if exists subscriptions_active_limit on subscriptions;
create trigger subscriptions_active_limit
    before insert or update of active on subscriptions
    for each row execute function enforce_active_subscription_limit();

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
    occurred_at timestamptz not null default now(),
    constraint subscription_errors_message_size_check check (char_length(error_message) between 1 and 1000)
);

alter table subscriptions enable row level security;
alter table notification_logs enable row level security;
alter table subscription_errors enable row level security;

-- L'application utilise uniquement la clé service_role côté serveur.
-- Aucune donnée d'abonnement n'est exposée directement au navigateur.