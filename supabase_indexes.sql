-- Index Supabase — colonnes temporelles des deux grosses tables.
--
-- Constat (mesuré le 2026-08-04, sessions ~100k lignes / padel_slots ~588k) :
--
--   ORDER BY releve DESC      sur sessions     → HTTP 500, statement timeout
--   ORDER BY dernier_vu DESC  sur padel_slots  → HTTP 500, statement timeout
--   ORDER BY date / WHERE brand_key / club_slug → 0,6-0,9 s, déjà couverts
--
-- Sans ces deux index, toute page qui veut « les derniers relevés, toutes
-- marques confondues » est impossible : Postgres doit trier la table entière.
-- Les dashboards actuels filtrent tous par marque ou par club, donc rien
-- n'est cassé aujourd'hui — mais un écran « fraîcheur du scrap » ou « flux
-- des dernières séances » se heurterait au mur.
--
-- Appliquer : Supabase → SQL Editor → coller → Run.
-- CONCURRENTLY évite de verrouiller les tables pendant la création ; en
-- contrepartie chaque instruction doit tourner hors transaction, donc à
-- lancer une par une si l'éditeur enveloppe le script dans un BEGIN.

CREATE INDEX CONCURRENTLY IF NOT EXISTS sessions_releve_desc_idx
    ON public.sessions (releve DESC NULLS LAST);

CREATE INDEX CONCURRENTLY IF NOT EXISTS padel_slots_dernier_vu_desc_idx
    ON public.padel_slots (dernier_vu DESC NULLS LAST);

-- Composites : servent les vues « une marque / un club sur une période »,
-- qui sont le motif de lecture réel des dashboards.
CREATE INDEX CONCURRENTLY IF NOT EXISTS sessions_brand_date_idx
    ON public.sessions (brand_key, date DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS padel_slots_club_date_idx
    ON public.padel_slots (club_slug, date DESC);

-- Vérification (doit lister les 4 index ci-dessus) :
--   SELECT indexname, indexdef FROM pg_indexes
--   WHERE tablename IN ('sessions','padel_slots') ORDER BY tablename, indexname;
--
-- Contrôle d'effet, après création — les deux doivent repasser sous la seconde :
--   /rest/v1/sessions?select=releve&order=releve.desc&limit=1
--   /rest/v1/padel_slots?select=dernier_vu&order=dernier_vu.desc&limit=1
