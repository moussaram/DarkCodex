-- DarkCodex — Paiements SasPay
-- A exécuter UNE FOIS dans le Supabase SQL Editor (après supabase_login.sql).
-- Le script peut être relancé sans danger.

-- 1. Table des intentions de paiement : chaque ligne lie une session SasPay
--    à UN utilisateur et à UN plan (le prix vient de la table plans).
create table if not exists public.paiements (
  id uuid default gen_random_uuid() primary key,
  user_id uuid not null references auth.users(id),
  plan text not null references public.plans(nom),
  tokens bigint not null,
  montant_fcfa int not null,
  session_id text unique not null,
  statut text not null default 'en_attente'
    check (statut in ('en_attente', 'credite', 'anomalie')),
  cree_a timestamp with time zone not null default now(),
  credite_a timestamp with time zone
);

-- Seul le serveur (service_role) lit/écrit : RLS activé, aucune policy.
alter table public.paiements enable row level security;

-- 2. Un même paiement ne peut JAMAIS être enregistré deux fois dans achats.
--    (Si cette commande échoue, il existe déjà des doublons dans achats :
--    supprime-les, puis relance le script.)
create unique index if not exists achats_transaction_id_unique
  on public.achats (transaction_id)
  where transaction_id is not null;

-- 3. Crédit de tokens atomique (un seul UPDATE, pas de lecture puis écriture).
create or replace function public.crediter_tokens(
  p_user_id uuid,
  p_tokens bigint,
  p_plan text
)
returns public.profils
language sql
security definer
set search_path = public
as $$
  update public.profils
     set tokens_solde = tokens_solde + p_tokens,
         plan = p_plan
   where id = p_user_id
  returning *;
$$;

-- IMPORTANT : sans ces deux lignes, n'importe qui pourrait s'offrir des tokens
-- en appelant la fonction avec la clé « anon ».
revoke all on function public.crediter_tokens(uuid, bigint, text) from public, anon, authenticated;
grant execute on function public.crediter_tokens(uuid, bigint, text) to service_role;
