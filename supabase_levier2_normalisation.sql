-- ============================================================================
-- LEVIER 2 — remplacer les clés texte par des entiers
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


-- ============================================================================
-- BLOC 1 — correspondances et backfill (rien n'est supprimé)
-- ============================================================================

-- 1a. Un identifiant numérique stable par club.
ALTER TABLE public.padel_clubs
    ADD COLUMN IF NOT EXISTS club_id smallint;

CREATE SEQUENCE IF NOT EXISTS public.padel_clubs_club_id_seq AS smallint;

UPDATE public.padel_clubs
   SET club_id = nextval('public.padel_clubs_club_id_seq')
 WHERE club_id IS NULL;

ALTER TABLE public.padel_clubs
    ALTER COLUMN club_id SET DEFAULT nextval('public.padel_clubs_club_id_seq');

CREATE UNIQUE INDEX IF NOT EXISTS padel_clubs_club_id_uidx
    ON public.padel_clubs (club_id);

-- 1b. Un identifiant numérique stable par terrain, dans son club.
CREATE TABLE IF NOT EXISTS public.padel_courts (
    id         integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    club_slug  text NOT NULL,
    court_ref  text NOT NULL,
    UNIQUE (club_slug, court_ref)
);

INSERT INTO public.padel_courts (club_slug, court_ref)
SELECT DISTINCT club_slug, coalesce(court_id, '')
FROM public.padel_slots
ON CONFLICT (club_slug, court_ref) DO NOTHING;

-- 1c. Les nouvelles colonnes de padel_slots, backfillées.
ALTER TABLE public.padel_slots
    ADD COLUMN IF NOT EXISTS club_id   smallint,
    ADD COLUMN IF NOT EXISTS court_num integer;

UPDATE public.padel_slots s
   SET club_id = c.club_id
  FROM public.padel_clubs c
 WHERE c.slug = s.club_slug AND s.club_id IS DISTINCT FROM c.club_id;

UPDATE public.padel_slots s
   SET court_num = t.id
  FROM public.padel_courts t
 WHERE t.club_slug = s.club_slug
   AND t.court_ref = coalesce(s.court_id, '')
   AND s.court_num IS DISTINCT FROM t.id;

-- 1d. VÉRIFICATION BLOQUANTE — doit renvoyer 0. Si non, ne pas continuer :
--     des lignes n'ont pas trouvé leur correspondance et la bascule les
--     perdrait.
SELECT count(*) AS lignes_non_resolues
FROM public.padel_slots
WHERE club_id IS NULL OR court_num IS NULL;

-- 1e. Contrôle d'unicité sur la NOUVELLE clé. Doit renvoyer 0 ligne, sinon
--     l'index unique du bloc 2 échouerait.
SELECT club_id, date, heure, court_num, duree, count(*) AS n
FROM public.padel_slots
GROUP BY club_id, date, heure, court_num, duree
HAVING count(*) > 1
LIMIT 20;


-- ============================================================================
-- BLOC 2 — nouvel index unique (hors transaction, une instruction à la fois)
-- ============================================================================

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS padel_slots_metier2_uidx
    ON public.padel_slots (club_id, date, heure, court_num, duree);

SELECT indexrelid::regclass AS index, indisvalid AS valide
FROM pg_index WHERE indexrelid = 'padel_slots_metier2_uidx'::regclass;

-- >>> ARRÊT ICI. Pousser le sync qui envoie club_id / court_num, vérifier un
-- >>> run vert, PUIS seulement le bloc 3.


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
