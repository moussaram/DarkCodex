-- DarkCodex — correctifs 2 : packs de tokens, source unique des prix
-- A exécuter dans le Supabase SQL Editor. Relançable sans danger.

-- ═══ C4 : le terminal n'utilise plus de refresh_token Supabase ═══
alter table public.device_codes drop column if exists refresh_token;

-- ═══ C2 / C5 : la table plans est la SEULE source des prix ═══
insert into public.plans (nom, tokens, prix_fcfa, description) values
  ('gratuit', 5000,   0,    '5 000 tokens offerts à l''inscription'),
  ('starter', 50000,  500,  '50 000 tokens - Idéal pour débuter'),
  ('pro',     500000, 2500, '500 000 tokens - Pour les développeurs actifs')
on conflict (nom) do update
  set tokens      = excluded.tokens,
      prix_fcfa   = excluded.prix_fcfa,
      description = excluded.description;

-- Le nom officiel du plan gratuit est 'gratuit' (et non 'free')
update public.profils set plan = 'gratuit' where plan = 'free';

-- Les plans qui ne sont plus proposés ne doivent plus apparaître sur le site
-- (supprime seulement ceux qui n'ont jamais été payés)
delete from public.plans
 where nom in ('business', 'enterprise')
   and not exists (select 1 from public.paiements p where p.plan = plans.nom);

-- ═══ Contrôle : tu dois voir exactement 3 lignes ═══
select nom, tokens, prix_fcfa from public.plans order by prix_fcfa;

-- ═══ A lancer SEULEMENT si ces tables ont été créées à la main (elles ne
--     sont plus utilisées) et que tu n'y tiens aucune donnée utile ═══
-- drop table if exists public.abonnements;
-- drop table if exists public.transactions;
-- drop table if exists public.licences;   -- après avoir crédité d'éventuels clients Pro
