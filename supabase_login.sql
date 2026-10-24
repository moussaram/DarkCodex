-- DarkCodex — Système de connexion complet (Partie 1)
-- A exécuter dans le Supabase SQL Editor du projet.

-- Table profils utilisateurs
create table if not exists profils (
  id uuid references auth.users(id) primary key,
  email text unique not null,
  nom text,
  tokens_solde bigint default 5000,
  tokens_utilises bigint default 0,
  plan text default 'gratuit',
  date_inscription timestamp default now()
);

-- Table codes device (pour la connexion terminal)
create table if not exists device_codes (
  id uuid default gen_random_uuid() primary key,
  code text unique not null,
  user_id uuid references auth.users(id),
  token text,
  confirme boolean default false,
  expire_a timestamp default (now() + interval '10 minutes'),
  cree_a timestamp default now()
);

-- Table achats tokens
create table if not exists achats (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references auth.users(id),
  plan text not null,
  tokens_ajoutes bigint not null,
  montant_fcfa int not null,
  transaction_id text,
  date_achat timestamp default now()
);

-- Table plans disponibles
create table if not exists plans (
  nom text primary key,
  tokens bigint not null,
  prix_fcfa int not null,
  description text
);

insert into plans (nom, tokens, prix_fcfa, description) values
('gratuit', 5000, 0, '5 000 tokens offerts à l inscription'),
('starter', 50000, 500, '50 000 tokens - Idéal pour débuter'),
('pro', 500000, 2500, '500 000 tokens - Pour les développeurs actifs');

-- Créer profil automatiquement après inscription
create or replace function creer_profil()
returns trigger as $$
begin
  insert into profils (id, email, nom)
  values (new.id, new.email, new.raw_user_meta_data->>'full_name');
  return new;
end;
$$ language plpgsql security definer;

create trigger on_auth_user_created
after insert on auth.users
for each row execute procedure creer_profil();

-- ─────────────────────────────────────────────────────────────
-- Sécurité : seules les requêtes du serveur (service_role) doivent
-- lire/écrire ces tables. La clé anon est exposée dans le navigateur,
-- donc on active le RLS sans policy : le rôle anon n'a aucun accès,
-- la service_role (qui bypass le RLS) reste utilisée par server/.
-- Les tokens_utilises / tokens_solde ne doivent jamais être modifiables
-- depuis le client.
-- ─────────────────────────────────────────────────────────────
alter table profils enable row level security;
alter table device_codes enable row level security;
alter table achats enable row level security;
alter table plans enable row level security;

-- La liste des plans reste publiquement lisible (page /abonnements).
create policy "plans lecture publique" on plans
  for select using (true);