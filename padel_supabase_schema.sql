-- Tables dédiées padel (sur le même projet Supabase que brands/sessions).
-- À lancer en 2 étapes : (1) CREATE TABLE, (2) RLS + policies.

-- ============================================================
-- Étape 1 : tables (déjà fait si tu as suivi le plan initial)
-- ============================================================

create table if not exists padel_clubs (
  slug         text primary key,
  source       text not null,             -- anybuddy|urbanpadel|doinsport|playtomic
  unified_id   text,                      -- cluster cross-plateforme (cf. padel_club_unified.json)
  name         text,
  cp           text,
  city         text,
  lat          numeric,
  lng          numeric,
  meta         jsonb,
  updated_at   timestamptz default now()
);

create index if not exists padel_clubs_cp_idx       on padel_clubs(cp);
create index if not exists padel_clubs_source_idx   on padel_clubs(source);
create index if not exists padel_clubs_unified_idx  on padel_clubs(unified_id) where unified_id is not null;

create table if not exists padel_slots (
  id            text primary key,         -- "{club_slug}|{session_id}" — globalement unique
  club_slug     text references padel_clubs(slug) on delete cascade,
  date          date,
  heure         time,
  fin           time,
  duree         int,                      -- minutes (60/90/120 typiquement)
  terrain       text,
  court_id      text,
  prix          numeric,                  -- EUR
  statut        text,                     -- disponible | reserve
  finie         bool default false,
  source        text,
  premier_vu    text,                     -- 'YYYY-MM-DD HH:MM' format (texte simple)
  dernier_vu    text,
  updated_at    timestamptz default now()
);

create index if not exists padel_slots_date_idx       on padel_slots(date);
create index if not exists padel_slots_club_idx       on padel_slots(club_slug);
create index if not exists padel_slots_statut_idx     on padel_slots(statut);
create index if not exists padel_slots_source_idx     on padel_slots(source);
create index if not exists padel_slots_date_statut_idx on padel_slots(date, statut) where not finie;

-- ============================================================
-- Étape 2 : RLS + policies de lecture publique
-- À LANCER DANS UN 2ÈME RUN dans le SQL editor.
-- ============================================================

alter table padel_clubs enable row level security;
alter table padel_slots enable row level security;

drop policy if exists "anon read padel_clubs" on padel_clubs;
create policy "anon read padel_clubs" on padel_clubs
  for select to anon using (true);

drop policy if exists "anon read padel_slots" on padel_slots;
create policy "anon read padel_slots" on padel_slots
  for select to anon using (true);

-- ============================================================
-- (Optionnel) Vue agrégée pour le dashboard, évite de fetcher 50k+ slots
-- ============================================================

create or replace view padel_clubs_kpi as
select
  c.slug, c.source, c.unified_id, c.name, c.cp, c.city,
  count(s.id) filter (where not s.finie) as n_slots_a_venir,
  count(s.id) filter (where s.statut = 'reserve' and not s.finie) as n_reserves,
  count(s.id) filter (where s.statut = 'disponible' and not s.finie) as n_disponibles,
  avg(s.prix) filter (where s.prix > 0 and s.duree = 60) as prix_avg_60min,
  avg(s.prix) filter (where s.prix > 0 and s.duree = 90) as prix_avg_90min,
  avg(s.prix) filter (where s.prix > 0 and s.duree = 120) as prix_avg_120min,
  max(s.dernier_vu) as last_seen
from padel_clubs c
left join padel_slots s on s.club_slug = c.slug
group by c.slug, c.source, c.unified_id, c.name, c.cp, c.city;

-- Vue OK pour lecture anon via PostgREST
alter view padel_clubs_kpi set (security_invoker = true);
