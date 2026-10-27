-- DarkCodex — correctifs du système d'abonnement (27 octobre 2026)
-- A exécuter dans le Supabase SQL Editor. Relançable sans danger.

-- ═══ URGENCE (problème 5) : fermer l'accès public à l'ancienne table licences ═══
alter table if exists public.licences enable row level security;
revoke all on table public.licences from anon, authenticated;

-- ═══ Problème 3 : stocker le jeton de renouvellement du terminal ═══
alter table public.device_codes add column if not exists refresh_token text;

-- ═══ Problèmes 6 et 7 : débit de tokens ATOMIQUE ═══
-- Un seul UPDATE : pas de lecture puis écriture. Si le solde tombe à 0,
-- le plan repasse à 'gratuit' (plus de fonctions Pro sans tokens).
create or replace function public.debiter_tokens(
  p_user_id uuid,
  p_tokens bigint
)
returns public.profils
language sql
security definer
set search_path = public
as $$
update public.profils
set tokens_solde = greatest(0, tokens_solde - greatest(0, p_tokens)),
    tokens_utilises = tokens_utilises + greatest(0, p_tokens),
    plan = case
      when tokens_solde - greatest(0, p_tokens) <= 0 then 'gratuit'
      else plan
    end
where id = p_user_id
returning *;
$$;

revoke all on function public.debiter_tokens(uuid, bigint) from public, anon, authenticated;
grant execute on function public.debiter_tokens(uuid, bigint) to service_role;

-- ═══ Problème 2 : accélérer la recherche des paiements d'un utilisateur ═══
create index if not exists paiements_user_cree
  on public.paiements (user_id, cree_a desc);
