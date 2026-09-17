-- ============================================================================
-- LEVIER 1 — récupérer l'espace mangé par le ballonnement
-- ============================================================================
--
-- POURQUOI 559 MB ALORS QU'IL Y A ~200 MB DE DONNÉE
-- Le sync renvoyait le store ENTIER à chaque passage : 93 157 lignes toutes
-- les 30 min (IDF) et 85 209 toutes les 2 h (national), soit 5 494 044
-- réécritures par jour. En Postgres, un upsert n'écrase pas la ligne : il en
-- écrit une nouvelle version et marque l'ancienne morte. Cela fait ~1,1 GB de
-- tuples morts brassés quotidiennement. L'autovacuum les recycle pour les
-- écritures suivantes, mais ne rend JAMAIS l'espace au disque : la table
-- garde sa taille maximale atteinte.
--
-- VACUUM FULL réécrit la table à côté, sans les morts, puis bascule.
--
-- ⚠️ À FAIRE DANS CET ORDRE, sinon le gain est repris en quelques jours :
--    1. ce fichier ;
--    2. pousser le sync incrémental (déjà committé) — il divise le volume
--       d'écriture par ~20 à 60 et empêche le ballonnement de revenir.
--
-- ⚠️ L'éditeur SQL de Supabase enveloppe les scripts multi-instructions dans
--    une transaction, et VACUUM y est interdit. COLLER UNE SEULE LIGNE À LA
--    FOIS. Chacune verrouille sa table quelques minutes : lancer juste après
--    un run de sectors-padel.
--
-- Prévoir temporairement le double de la taille de la table sur le disque.
-- Le plafond de 500 MB est un quota de facturation, pas la taille du disque
-- provisionné : il reste de la place physique pour la réécriture.
-- ============================================================================


-- 1. Mesure AVANT — garder ce résultat pour comparer.
SELECT c.relname AS objet,
       pg_size_pretty(pg_total_relation_size(c.oid))          AS total,
       pg_size_pretty(pg_relation_size(c.oid))                AS donnees,
       pg_size_pretty(pg_total_relation_size(c.oid)
                      - pg_relation_size(c.oid))              AS index_et_toast
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY pg_total_relation_size(c.oid) DESC;

-- 1b. Combien de mortes ? C'est la mesure du gain à attendre.
SELECT relname, n_live_tup AS vivantes, n_dead_tup AS mortes,
       CASE WHEN n_live_tup > 0
            THEN round(100.0 * n_dead_tup / n_live_tup, 1) END AS pct_mortes,
       last_autovacuum
FROM pg_stat_user_tables
WHERE relname IN ('padel_slots', 'sessions', 'padel_clubs')
ORDER BY n_dead_tup DESC;


-- 2. Récupération. UNE LIGNE À LA FOIS.

VACUUM FULL public.padel_slots;

ANALYZE public.padel_slots;

REINDEX TABLE CONCURRENTLY public.padel_slots;

VACUUM FULL public.sessions;

ANALYZE public.sessions;

REINDEX TABLE CONCURRENTLY public.sessions;


-- 3. Mesure APRÈS
SELECT pg_size_pretty(pg_database_size(current_database())) AS base_totale;
