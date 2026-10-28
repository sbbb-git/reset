-- ============================================================================
-- padel_stats_horaire : le padel HORS Île-de-France passe à la maille JOUR
-- ============================================================================
-- La table est passée de 60 à 93 Mo en une semaine (28/10) : ~1 000 clubs
-- nationaux agrégés par club × jour × HEURE × durée. Pour l'IDF (cœur de
-- l'analyse), on garde l'heure ; hors IDF, la journée suffit : les lignes
-- sont repliées sur heure = 00:00 (sentinelle « journée entière »), ~12× moins
-- de lignes. Totaux conservés (créneaux, réservés, prix min/max, prix moyen
-- pondéré).
--
-- 1. padel_rollup_jour() applique la règle aux prochains jours repliés.
-- 2. L'historique national est replié mois par mois (délai de l'API).
-- 3. VACUUM FULL pour rendre la place.
-- Idempotent : relancer ne replie que ce qui reste à l'heure.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.padel_rollup_jour(jour date)
RETURNS TABLE (lignes bigint, buckets bigint)
LANGUAGE plpgsql AS $$
DECLARE
    n_brut     bigint;
    n_buckets  bigint;
    n_couvert  bigint;
    n_supprime bigint;
BEGIN
    SELECT count(*) INTO n_brut
      FROM public.padel_slots WHERE date = jour;

    IF n_brut = 0 THEN
        RETURN QUERY SELECT 0::bigint, 0::bigint;
        RETURN;
    END IF;

    INSERT INTO public.padel_stats_horaire AS t
        (club_slug, date, heure, duree, n_creneaux, n_reserves,
         prix_min, prix_moy, prix_max, source)
    SELECT s.club_slug, s.date,
           CASE WHEN c.metro IS NOT NULL AND c.metro <> 'idf' THEN '00:00' ELSE s.heure END,
           coalesce(s.duree, 0)::smallint,
           least(count(*), 32767)::smallint,
           least(count(*) FILTER (WHERE s.statut = 'reserve'), 32767)::smallint,
           min(s.prix), avg(s.prix), max(s.prix), min(s.source)
      FROM public.padel_slots s
      LEFT JOIN public.padel_clubs c ON c.slug = s.club_slug
     WHERE s.date = jour
     GROUP BY 1, 2, 3, 4
    ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
       SET n_creneaux = excluded.n_creneaux,
           n_reserves = excluded.n_reserves,
           prix_min   = excluded.prix_min,
           prix_moy   = excluded.prix_moy,
           prix_max   = excluded.prix_max,
           source     = excluded.source;

    GET DIAGNOSTICS n_buckets = ROW_COUNT;

    SELECT coalesce(sum(n_creneaux), 0) INTO n_couvert
      FROM public.padel_stats_horaire WHERE date = jour;

    IF n_couvert < n_brut THEN
        RAISE EXCEPTION
          'Rollup incomplet pour le % : % lignes brutes, % couvertes. '
          'Aucune suppression.', jour, n_brut, n_couvert;
    END IF;

    DELETE FROM public.padel_slots WHERE date = jour;
    GET DIAGNOSTICS n_supprime = ROW_COUNT;

    IF n_supprime <> n_brut THEN
        RAISE EXCEPTION
          'Incohérence pour le % : % comptées, % supprimées.',
          jour, n_brut, n_supprime;
    END IF;

    RETURN QUERY SELECT n_brut, n_buckets;
END $$;

-- 2. Historique national replié, par tranches (le plus ancien d'abord).

WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2000-01-01' AND t.date < DATE '2026-06-01' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-06-01' AND t.date < DATE '2026-06-16' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-06-16' AND t.date < DATE '2026-07-01' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-07-01' AND t.date < DATE '2026-07-16' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-07-16' AND t.date < DATE '2026-08-01' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-08-01' AND t.date < DATE '2026-08-16' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-08-16' AND t.date < DATE '2026-09-01' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-09-01' AND t.date < DATE '2026-09-16' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-09-16' AND t.date < DATE '2026-10-01' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-10-01' AND t.date < DATE '2026-10-16' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-10-16' AND t.date < DATE '2026-11-01' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-11-01' AND t.date < DATE '2026-11-16' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-11-16' AND t.date < DATE '2026-12-01' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-12-01' AND t.date < DATE '2026-12-16' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


WITH nat AS (SELECT slug FROM public.padel_clubs WHERE metro IS NOT NULL AND metro <> 'idf'),
src AS (
  DELETE FROM public.padel_stats_horaire t USING nat
   WHERE t.club_slug = nat.slug AND t.date >= DATE '2026-12-16' AND t.date < DATE '2027-01-01' AND t.heure <> '00:00'
  RETURNING t.*)
INSERT INTO public.padel_stats_horaire AS t
    (club_slug, date, heure, duree, n_creneaux, n_reserves, prix_min, prix_moy, prix_max, source)
SELECT club_slug, date, '00:00', duree,
       least(sum(n_creneaux), 32767)::smallint, least(sum(n_reserves), 32767)::smallint,
       min(prix_min), sum(prix_moy * n_creneaux) / nullif(sum(n_creneaux), 0), max(prix_max), min(source)
  FROM src GROUP BY club_slug, date, duree
ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
   SET prix_moy   = (t.prix_moy * t.n_creneaux + excluded.prix_moy * excluded.n_creneaux)
                    / nullif(t.n_creneaux + excluded.n_creneaux, 0),
       n_creneaux = least(t.n_creneaux + excluded.n_creneaux, 32767),
       n_reserves = least(t.n_reserves + excluded.n_reserves, 32767),
       prix_min   = least(t.prix_min, excluded.prix_min),
       prix_max   = greatest(t.prix_max, excluded.prix_max);


-- 3. Rendre la place.
VACUUM FULL public.padel_stats_horaire;
ANALYZE public.padel_stats_horaire;

SELECT pg_size_pretty(pg_total_relation_size('public.padel_stats_horaire')) AS stats_horaire,
       (SELECT count(*) FROM public.padel_stats_horaire) AS lignes;
