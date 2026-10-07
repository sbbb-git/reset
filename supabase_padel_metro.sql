-- ============================================================================
-- padel_clubs.metro — colonne déclarée dans padel_supabase_schema.sql mais
-- jamais appliquée en base.
-- ============================================================================
-- Le sync national l'envoie à chaque club (« idf » ou code département) :
-- sans elle, PostgREST refuse tout l'upsert (PGRST204 « Could not find the
-- 'metro' column ») et AUCUN créneau national n'atteint la base. Le sync
-- incrémental le confirme : le store national n'a jamais reçu une seule
-- empreinte _sync_h.
--
-- Idempotent. Le NOTIFY recharge le cache de schéma de PostgREST, sans quoi
-- l'API continuerait d'ignorer la colonne pendant quelques minutes.
-- ============================================================================

ALTER TABLE public.padel_clubs ADD COLUMN IF NOT EXISTS metro text;
CREATE INDEX IF NOT EXISTS padel_clubs_metro_idx ON public.padel_clubs (metro);
NOTIFY pgrst, 'reload schema';

-- Contrôle : la colonne existe, et taille des tables avant l'arrivée du
-- national.
SELECT column_name, data_type
  FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name = 'padel_clubs'
 ORDER BY ordinal_position;

SELECT c.relname,
       c.reltuples::bigint                           AS lignes_estimees,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS taille_totale
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind = 'r'
 ORDER BY pg_total_relation_size(c.oid) DESC
 LIMIT 12;
