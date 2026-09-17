-- ============================================================================
-- LEVIER 2b — BASCULE (destructif)
-- ============================================================================
--
-- MESURÉ SUR LES DONNÉES RÉELLES (93 157 créneaux IDF, 2026-09-17)
--   143 clubs distincts      -> tient dans un smallint (2 octets)
--   465 terrains distincts   -> tient dans un smallint (2 octets)
--   club_slug : 26 caractères en moyenne (max 52)  -> 27 octets stockés
--   court_id  : 33 caractères en moyenne (max 36)  -> 34 octets stockés
--   terrain   : 16 caractères, 86 % de la forme « Terrain <hex> », donc
--               dérivables de court_id
--
-- GAIN ATTENDU — recalculé sur la mesure réelle du 2026-09-17
--   padel_slots            206 MB   189 o/ligne   ->  ~123 MB   (-83)
--   padel_slots_metier_uidx 128 MB   118 o/entrée  ->   ~33 MB   (-95)
--   ------------------------------------------------------------------
--   ~178 MB une fois, et chaque ligne future coûte 34 % de moins.
--
--   (Mon estimation précédente disait ~79 MB : elle s'appuyait sur les
--   tailles du 9 août, or l'index unique a doublé depuis — 67 -> 128 MB.
--   Plus la table grossit, plus ce levier rapporte.)
--
--   Pourquoi l'index coûte si cher : sa clé est
--   (club_slug, date, heure, court_id, duree), soit ~87 octets de texte par
--   entrée pour un index dont le seul rôle est de garantir l'unicité à
--   l'upsert. Avec des entiers la clé tombe à ~18 octets.
--
-- ⚠️ AVANT DE LANCER CECI : faire le levier 1 et pousser le sync incrémental,
--    puis REMESURER. Si la base est retombée autour de 250-300 MB, ce levier
--    devient une optimisation de confort et non une urgence — et il vaut mieux
--    le jouer à froid qu'au-dessus du quota.
--
-- ⚠️ court_id ne peut pas devenir un uuid : urbanpadel expose des ids
--    numériques (« 12 », « 14 »). D'où une table de correspondance plutôt
--    qu'une conversion de type.
--
-- ORDRE D'EXÉCUTION — à respecter
--   1. Bloc 1  : tables de correspondance + colonnes, backfill  (non bloquant)
--   2. Bloc 2  : nouvel index unique                            (CONCURRENTLY)
--   3. Pousser padel_supabase_sync.py qui envoie les ids
--   4. Vérifier qu'un run de sectors-padel passe au vert
--   5. Bloc 3  : bascule + suppression des colonnes texte + VACUUM FULL
-- Entre 2 et 5, les deux schémas coexistent et le sync fonctionne.
-- ============================================================================


-- ⚠️ NE JOUER QU'APRÈS levier2a, ET après avoir poussé le sync qui envoie
-- club_id / court_num, ET vérifié qu'un run de sectors-padel passe au
-- vert. Ce fichier supprime les colonnes texte : si le sync les envoie
-- encore, il casse.
-- Contrôle préalable obligatoire (doit renvoyer 0) :
--   SELECT count(*) FROM public.padel_slots
--    WHERE club_id IS NULL OR court_num IS NULL;

-- ============================================================================
-- BLOC 3 — bascule et libération de l'espace
-- ============================================================================

BEGIN;

    -- terrain : 86 % sont « Terrain <hex> », dérivable de court_id. On ne
    -- vide QUE ceux-là ; le tiers restant porte un vrai nom de terrain et
    -- doit être conservé.
    UPDATE public.padel_slots
       SET terrain = NULL
     WHERE terrain ~ '^Terrain [0-9a-f]+$';

    DROP INDEX IF EXISTS public.padel_slots_metier_uidx;
    DROP INDEX IF EXISTS public.padel_slots_club_date_idx;

    ALTER TABLE public.padel_slots DROP COLUMN IF EXISTS club_slug;
    ALTER TABLE public.padel_slots DROP COLUMN IF EXISTS court_id;

COMMIT;

-- Hors transaction, une ligne à la fois :
VACUUM FULL public.padel_slots;
ANALYZE public.padel_slots;


-- ============================================================================
-- BLOC 4 — contrôle
-- ============================================================================
SELECT c.relname AS objet, pg_size_pretty(pg_relation_size(c.oid)) AS taille
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname LIKE 'padel_slots%'
ORDER BY pg_relation_size(c.oid) DESC;

SELECT pg_size_pretty(pg_database_size(current_database())) AS base_totale;
SELECT count(*) AS lignes FROM public.padel_slots;   -- doit être inchangé
