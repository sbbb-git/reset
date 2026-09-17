-- ============================================================================
-- DIAGNOSTIC — lecture seule, rien n'est modifié
-- ============================================================================
-- Sert à choisir le bon correctif plutôt qu'à deviner. Le rollup a expiré sur
--   SELECT count(*) FROM padel_slots WHERE date::date < limite
-- et il faut savoir pourquoi : type réel de la colonne `date` (un cast sur du
-- texte interdit l'usage d'un index), index présents, délai en vigueur.
--
-- Toutes les requêtes ici sont bornées ou lisent des catalogues : aucune ne
-- balaie la table.
-- ============================================================================

-- 1. Délai maximal d'une instruction, tel qu'il s'applique à ce rôle.
SELECT current_setting('statement_timeout') AS statement_timeout,
       current_user                          AS role_courant;

-- 2. Types réels des colonnes de padel_slots.
SELECT column_name, data_type, character_maximum_length
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'padel_slots'
ORDER BY ordinal_position;

-- 3. Index existants — y a-t-il de quoi filtrer sur la date ?
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public' AND tablename IN ('padel_slots', 'padel_stats_horaire')
ORDER BY tablename, indexname;

-- 4. Nombre de lignes ESTIMÉ (pg_class, instantané — surtout pas count(*)).
SELECT relname,
       reltuples::bigint            AS lignes_estimees,
       pg_size_pretty(pg_relation_size(oid)) AS taille
FROM pg_class
WHERE relname IN ('padel_slots', 'padel_stats_horaire', 'sessions');

-- 5. Étendue des dates, via les statistiques du planificateur (instantané).
SELECT attname, n_distinct,
       (most_common_vals::text)[1:120] AS extrait_valeurs_frequentes
FROM pg_stats
WHERE schemaname = 'public' AND tablename = 'padel_slots'
  AND attname IN ('date', 'statut');

-- 6. Plan de la requête qui a expiré, SANS l'exécuter.
EXPLAIN SELECT count(*) FROM public.padel_slots
 WHERE date::date < (current_date - 90);

-- 7. Même chose sans le cast, pour comparer les deux plans.
EXPLAIN SELECT count(*) FROM public.padel_slots
 WHERE date < (current_date - 90);
