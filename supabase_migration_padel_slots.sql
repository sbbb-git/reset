-- ============================================================================
-- Migration padel_slots : clé primaire texte → bigserial
-- ============================================================================
--
-- POURQUOI
-- La PK actuelle est une chaîne qui recolle des colonnes déjà stockées :
--     "padelshot-saint-quentin-en-yvelines-trappes|2026-06-15T10:00|<uuid>|60"
-- Longueur mesurée : min 35, moyenne 78, max 109 caractères, sur 588 k lignes.
-- Elle coûte deux fois : dans la table (~46 MB) et dans son index B-tree
-- (115,9 MB mesurés côté Supabase), parce qu'un B-tree recopie la clé dans
-- chaque nœud — avec 78 octets par entrée, il faut beaucoup de pages.
--
-- Une PK bigint tombe à 8 octets : ~5× plus d'entrées par page de 8 ko,
-- arbre plus plat, index ~10× plus petit.
--
-- GAIN ATTENDU  (le bloc 1 donne les chiffres réels de TA base)
--     colonne id      ~46 MB  →   ~5 MB
--     index pkey     ~116 MB  →  ~13 MB
--     nouvel index unique métier      +~50 MB
--     club_idx devient redondant       -12 MB
--     ------------------------------------------
--     net ≈ -105 MB   (468 MB → ~360 MB)
--
-- CE QUI N'EST PAS FAIT, ET POURQUOI
--   · court_id reste en text : urbanpadel expose des ids numériques ("12"),
--     0/80 sont des UUID valides — la conversion casserait cette source.
--   · terrain est conservée : seuls 67 % valent "Terrain <8 hex>" (dérivable
--     de court_id), le tiers restant porte un vrai nom de terrain.
--
-- ORDRE D'EXÉCUTION — à respecter, sinon le sync casse
--   1. Bloc 1 (lecture seule)     : mesurer, vérifier l'absence de doublons
--   2. Bloc 2                     : créer la contrainte UNIQUE métier
--   3. Pousser le patch Python    : on_conflict → colonnes métier
--   4. Bloc 3                     : basculer la PK + VACUUM FULL
-- Entre 2 et 4 le pipeline reste fonctionnel dans les deux versions.
--
-- Le scrape padel tourne toutes les 30 min et VACUUM FULL verrouille la
-- table quelques minutes : lancer le bloc 3 juste après un run.
-- ============================================================================


-- ============================================================================
-- BLOC 1 — MESURE (lecture seule, rien n'est modifié)
-- ============================================================================
-- Version « un seul copier-coller » : l'éditeur SQL de Supabase n'affiche que
-- le dernier résultat quand on enchaîne plusieurs SELECT, donc tout est
-- rassemblé en une table (metrique, valeur). Les requêtes détaillées 1a/1b/1c
-- restent en dessous si tu veux les jouer séparément.
--
-- LA SEULE LIGNE QUI DÉCIDE :
--   « >>> DOUBLONS SUR LA CLÉ MÉTIER » doit valoir 0.
--   Si elle vaut autre chose, NE PAS lancer le bloc 2 : la contrainte UNIQUE
--   échouerait. Me l'envoyer, on tranchera lequel garder.

WITH tailles AS (
    SELECT c.relname AS objet, pg_relation_size(c.oid) AS octets
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname LIKE 'padel_slots%'
), cle AS (
    SELECT count(*)                    AS lignes,
           round(avg(length(id)))      AS id_moyen,
           max(length(id))             AS id_max,
           sum(length(id) + 1)::bigint AS poids_id
    FROM public.padel_slots
), doublons AS (
    SELECT count(*) AS n FROM (
        SELECT 1 FROM public.padel_slots
        GROUP BY club_slug, date, heure, court_id, duree
        HAVING count(*) > 1
    ) x
)
SELECT '1. base totale'                  AS metrique, pg_size_pretty(pg_database_size(current_database())) AS valeur
UNION ALL SELECT '2. objet · ' || objet, pg_size_pretty(octets) FROM tailles
UNION ALL SELECT '3. lignes padel_slots', to_char(lignes, 'FM999G999G999') FROM cle
UNION ALL SELECT '4. id : longueur moyenne (car)', id_moyen::text FROM cle
UNION ALL SELECT '5. id : longueur max (car)', id_max::text FROM cle
UNION ALL SELECT '6. id : poids de la colonne', pg_size_pretty(poids_id) FROM cle
UNION ALL SELECT '7. id : poids si bigint', pg_size_pretty(lignes * 8) FROM cle
UNION ALL SELECT '>>> DOUBLONS SUR LA CLÉ MÉTIER (doit valoir 0)', n::text FROM doublons
ORDER BY 1;

-- Si la requête ci-dessus dépasse le statement_timeout, jouer le compte de
-- doublons seul — c'est lui qui coûte (GROUP BY sur toute la table) :
--   SELECT count(*) FROM (SELECT 1 FROM public.padel_slots
--     GROUP BY club_slug, date, heure, court_id, duree HAVING count(*) > 1) x;


-- --- variantes détaillées (facultatif) --------------------------------------

-- 1a. Poids actuel de chaque objet
SELECT c.relname AS objet,
       pg_size_pretty(pg_relation_size(c.oid)) AS taille
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND (c.relname = 'padel_slots' OR c.relname LIKE 'padel_slots%idx'
       OR c.relname = 'padel_slots_pkey')
ORDER BY pg_relation_size(c.oid) DESC;

-- 1b. Ce que coûte réellement la clé texte
SELECT count(*)                                   AS lignes,
       round(avg(length(id)))                     AS id_moyen_car,
       max(length(id))                            AS id_max_car,
       pg_size_pretty(sum(length(id) + 1)::bigint) AS poids_colonne_id,
       pg_size_pretty((count(*) * 8)::bigint)      AS poids_si_bigint
FROM public.padel_slots;

-- 1c. VÉRIFICATION BLOQUANTE — doit renvoyer 0 ligne.
--     Si des doublons sortent, ne pas exécuter le bloc 2 : la contrainte
--     UNIQUE échouerait. Me les envoyer, on tranchera lequel garder.
SELECT club_slug, date, heure, court_id, duree, count(*) AS n
FROM public.padel_slots
GROUP BY club_slug, date, heure, court_id, duree
HAVING count(*) > 1
LIMIT 20;


-- ============================================================================
-- BLOC 2 — CONTRAINTE D'UNICITÉ MÉTIER
-- ============================================================================
-- duree fait partie de la clé : 21 % des créneaux mesurés existent en
-- plusieurs durées sur le même terrain à la même heure (60 / 90 / 120 min).
-- L'omettre fusionnerait ces lignes et perdrait des réservations.
--
-- CONCURRENTLY pour ne pas verrouiller la table pendant la construction.
-- À lancer instruction par instruction (CONCURRENTLY interdit en transaction).

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS padel_slots_metier_uidx
    ON public.padel_slots (club_slug, date, heure, court_id, duree);

-- Contrôle : l'index doit être "valid" avant de continuer.
SELECT indexrelid::regclass AS index, indisvalid AS valide
FROM pg_index
WHERE indexrelid = 'padel_slots_metier_uidx'::regclass;

-- >>> ARRÊT ICI. Pousser le patch padel_supabase_sync.py, vérifier qu'un run
-- >>> de sectors-padel.yml passe au vert, PUIS seulement lancer le bloc 3.


-- ============================================================================
-- BLOC 3 — BASCULE DE LA CLÉ PRIMAIRE
-- ============================================================================
-- Transactionnel : si quoi que ce soit échoue, rien n'est appliqué.

BEGIN;

    -- L'ancienne PK laisse la place à la contrainte métier du bloc 2.
    ALTER TABLE public.padel_slots DROP CONSTRAINT IF EXISTS padel_slots_pkey;

    -- L'id texte n'apporte plus rien : ses composants sont tous en colonnes.
    ALTER TABLE public.padel_slots DROP COLUMN IF EXISTS id;

    -- Nouvelle PK technique, 8 octets.
    ALTER TABLE public.padel_slots ADD COLUMN id bigint GENERATED ALWAYS AS IDENTITY;
    ALTER TABLE public.padel_slots ADD CONSTRAINT padel_slots_pkey PRIMARY KEY (id);

    -- club_idx devient un préfixe de padel_slots_metier_uidx → redondant.
    DROP INDEX IF EXISTS public.padel_slots_club_idx;

COMMIT;

-- Rendre l'espace au disque. Un DELETE ou un DROP COLUMN ne le libère jamais
-- seul : Postgres marque l'espace réutilisable mais ne le restitue pas à l'OS.
-- VACUUM FULL réécrit la table à côté puis bascule → verrou exclusif de
-- quelques minutes, et il faut temporairement le double de la taille sur le
-- disque (largement couvert : 2 GB provisionnés pour ~470 MB utilisés).
VACUUM FULL public.padel_slots;
ANALYZE public.padel_slots;


-- ============================================================================
-- BLOC 4 — CONTRÔLE APRÈS MIGRATION
-- ============================================================================

-- 4a. Nouveau poids — padel_slots_pkey doit être passé de ~116 MB à ~13 MB
SELECT c.relname AS objet,
       pg_size_pretty(pg_relation_size(c.oid)) AS taille
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname LIKE 'padel_slots%'
ORDER BY pg_relation_size(c.oid) DESC;

-- 4b. Taille totale de la base
SELECT pg_size_pretty(pg_database_size(current_database())) AS base_totale;

-- 4c. Intégrité : le compte doit être identique à celui du bloc 1b
SELECT count(*) AS lignes FROM public.padel_slots;
