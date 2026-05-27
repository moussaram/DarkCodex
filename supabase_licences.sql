create table if not exists public.licences (
  key text unique not null,
  email text,
  actif boolean not null default true,
  created_at timestamp with time zone not null default now()
);
