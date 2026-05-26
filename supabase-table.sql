-- Run in Supabase SQL Editor (create the transactions table for auto-detect payment)
-- This is the same schema used by the web-test reference project

create table if not exists public.transactions (
  id bigint generated always as identity primary key,
  trx_id text not null unique,
  amount numeric not null,
  status text not null default 'unused'
);

-- Optional: timestamp for sorting (not required for polling)
-- alter table public.transactions add column if not exists created_at timestamptz default now();

alter table public.transactions enable row level security;

create policy "anon_insert" on public.transactions for insert to anon with check (true);
create policy "anon_select" on public.transactions for select to anon using (true);
create policy "anon_update" on public.transactions for update to anon using (true);
