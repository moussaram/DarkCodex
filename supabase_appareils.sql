-- DarkCodex — Appareils connectés (jetons longue durée pour le terminal)
-- A exécuter UNE FOIS dans le Supabase SQL Editor. Peut être relancé sans danger.

create table if not exists public.appareils (
  id uuid default gen_random_uuid() primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  email text,
  nom text,
  token_hash text unique not null, -- empreinte SHA-256 : le jeton lui-même n'est jamais stocké
  revoque boolean not null default false,
  cree_a timestamp with time zone not null default now()
);

-- Seul le serveur (service_role) lit/écrit : RLS activé, aucune policy.
alter table public.appareils enable row level security;
