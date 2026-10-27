-- DarkCodex — Parrainage
-- A exécuter UNE FOIS dans le Supabase SQL Editor (après supabase_login.sql). Peut être relancé sans danger.

alter table public.profils add column if not exists code_parrainage text unique;
alter table public.profils add column if not exists parraine_par uuid references auth.users(id);

create table if not exists public.parrainages (
    id uuid default gen_random_uuid() primary key,
    parrain_id uuid not null references auth.users(id),
    filleul_id uuid not null unique references auth.users(id), -- un compte ne peut être parrainé qu'une fois
    bonus bigint not null,
    cree_a timestamp with time zone not null default now()
);

-- Seul le serveur (service_role) lit/écrit : RLS activé, aucune policy.
alter table public.parrainages enable row level security;

-- Tout se passe dans UNE fonction (= une transaction) : les deux comptes sont crédités ou aucun.
create or replace function public.utiliser_code_parrainage(
    p_user_id uuid,
    p_code text,
    p_bonus bigint,
    p_max_filleuls int,
    p_jours int
)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
    v_parrain uuid;
    v_deja uuid;
    v_cree timestamp with time zone;
    v_nb int;
begin
    select id into v_parrain from public.profils where code_parrainage = upper(p_code);
    if v_parrain is null then return 'code_inconnu'; end if;
    if v_parrain = p_user_id then return 'code_personnel'; end if;

    -- Verrou sur la ligne du filleul : deux demandes simultanées ne peuvent pas toutes deux réussir.
    select parraine_par into v_deja from public.profils where id = p_user_id for update;
    if not found then return 'profil_introuvable'; end if;
    if v_deja is not null then return 'deja_parraine'; end if;

    select created_at into v_cree from auth.users where id = p_user_id;
    if v_cree is null or v_cree < now() - make_interval(days => p_jours) then
        return 'compte_trop_ancien'; -- le code sert à l'inscription, pas à un vieux compte
    end if;

    if exists (select 1 from public.parrainages where parrain_id = p_user_id and filleul_id = v_parrain) then
        return 'code_invalide'; -- pas de parrainage croisé entre deux comptes
    end if;

    select count(*) into v_nb from public.parrainages where parrain_id = v_parrain;
    if v_nb >= p_max_filleuls then return 'parrain_complet'; end if;

    update public.profils set parraine_par = v_parrain, tokens_solde = tokens_solde + p_bonus where id = p_user_id;
    update public.profils set tokens_solde = tokens_solde + p_bonus where id = v_parrain;

    insert into public.parrainages (parrain_id, filleul_id, bonus) values (v_parrain, p_user_id, p_bonus);

    return 'ok';
end;
$$;

-- IMPORTANT : sans ces deux lignes, n'importe qui pourrait s'offrir des tokens via la clé « anon ».
revoke all on function public.utiliser_code_parrainage(uuid, text, bigint, int, int) from public, anon, authenticated;
grant execute on function public.utiliser_code_parrainage(uuid, text, bigint, int, int) to service_role;
