-- Table de grilles tarifaires factuelles par marque.
-- Une ligne par marque, JSONB pour stocker toutes les variantes (packs N, abos avec engagement, etc.)
-- sans schema rigide qui varie selon le studio.

create table if not exists brand_prices (
  brand_key      text primary key references brands(key) on delete cascade,
  source_url     text not null,
  confiance      text not null default 'moyenne',  -- 'haute' | 'moyenne' | 'basse'
  scraped_at     timestamptz default now(),
  drop_in        numeric,
  -- packs : liste ordonnée par taille croissante
  -- [{"name":"Pack 10 séances","size":10,"prix_total":249,"prix_unitaire":24.9}, ...]
  packs          jsonb default '[]'::jsonb,
  -- abos mensuels : liste avec engagement, séances incluses, prix mensuel
  -- [{"name":"Abo 8/mois 12 mois","prix_mensuel":189,"seances_inclus":8,"engagement_mois":12}, ...]
  abos           jsonb default '[]'::jsonb,
  -- offres spéciales (essai, bienvenue, mother's day) : optionnel
  offres         jsonb default '[]'::jsonb,
  note           text
);

create index if not exists brand_prices_confiance_idx on brand_prices(confiance);

-- RLS : lecture anon comme pour brands/sessions
alter table brand_prices enable row level security;
drop policy if exists "anon read brand_prices" on brand_prices;
create policy "anon read brand_prices" on brand_prices for select to anon using (true);
