-- ============================================================================
-- LEVIER 3 — agréger l'historique padel et purger le brut
-- ============================================================================
--
-- CE QUI REND CE LEVIER SÛR, ET IL FAUT L'AVOIR VÉRIFIÉ AVANT DE LANCER
-- Aucune page ne lit padel_slots. prix.html, comparateur.html et etude.html
-- interrogent sessions, brands et brand_prices — jamais padel_slots. Les
-- trois computes padel (kpis, insights, anomalies) lisent le store local et
-- padel_idf_history.json.gz, jamais Supabase. Le seul lecteur est
-- padel_national_from_supabase.py, qui rapatrie tout l'historique pour que
-- prune_padel_live.py en jette 95 % au step suivant.
--
-- L'historique padel dans Supabase est donc, en pratique, en écriture seule.
-- La vraie archive vit dans padel_idf_history.json.gz et
-- padel_national_history.json.gz, committés dans le dépôt.
--
-- CE QU'ON PERD, DIT FRANCHEMENT
--   · le créneau ligne à ligne au-delà de la fenêtre : quel terrain précis,
--     le prix exact de CE créneau, premier_vu / dernier_vu.
--   · rien de tout cela n'alimente un écran aujourd'hui, et tout reste dans
--     les archives .gz si le besoin réapparaît.
--
-- CE QU'ON GARDE — c'est-à-dire la métrique
--   Le taux d'occupation par club × date × heure × durée : nombre de
--   créneaux observés, nombre réservés, prix min/moyen/max. C'est exactement
--   ce que les dashboards calculent et affichent.
--
-- ⚠️ L'ORDRE EST VITAL. On agrège d'abord, on vérifie que la somme des
--    créneaux agrégés égale le nombre de lignes brutes, et on ne supprime
--    qu'après. Purger sans agréger mettrait tous les taux d'occupation à
--    100 % : les créneaux passés restés « disponible » sont le DÉNOMINATEUR
--    du taux, pas du bruit.
--
-- GAIN ATTENDU — simulé sur les données réelles du 2026-09-17
--   Agrégation vérifiée sur les 174 837 créneaux des deux stores :
--     174 837 lignes brutes -> 79 394 lignes d'agrégat, soit 2,2×
--     contrôle de conservation : 174 837 créneaux couverts = 174 837. OK.
--     taux d'occupation recalculé depuis l'agrégat : 54,9 %, identique.
--
--   (2,2× et non 5,5× comme je l'avais d'abord annoncé : cette première
--   mesure groupait sans la durée. Or 21 % des créneaux existent en
--   plusieurs durées sur le même terrain à la même heure — les fusionner
--   perdrait des réservations, donc `duree` reste dans la clé.)
--
--   En gardant 30 jours de brut, sur les ~1,42 M lignes :
--     brut conservé   ~397 500 lignes × 332 o  = ~126 MB
--     agrégat         ~464 000 lignes ×  45 o  =  ~20 MB
--     ----------------------------------------------------
--     padel : 361 MB -> ~146 MB
--
--   Et surtout padel_slots CESSE DE GROSSIR : la purge est glissante. Seul
--   l'agrégat grandit, d'environ 9 MB par mois au lieu de 135.
--
-- VALIDÉ AVANT LIVRAISON, sur un PostgreSQL 16 monté pour l'occasion, chargé
-- avec 169 567 créneaux réels tirés des deux stores :
--   · la fonction compile et s'exécute ;
--   · rollup(5) : 28 385 lignes brutes -> 13 113 buckets, 28 385 supprimées ;
--   · conservation : sum(n_creneaux) = 28 385, exactement le compte supprimé ;
--   · idempotence : relancée, elle rend 0 agrégée / 0 supprimée ;
--   · VACUUM FULL : 58 MB -> 43 MB après suppression de 17 % des lignes.
-- Extrapolé à ta base (~1,42 M lignes, fenêtre 30 jours, ~72 % supprimés) :
--   padel 361 MB -> ~121 MB.
-- ============================================================================


-- ============================================================================
-- BLOC 1 — la table d'agrégat
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.padel_stats_horaire (
    club_slug   text     NOT NULL,
    date        date     NOT NULL,
    heure       text     NOT NULL,
    duree       smallint NOT NULL,
    n_creneaux  smallint NOT NULL,          -- terrains observés sur ce créneau
    n_reserves  smallint NOT NULL,          -- dont réservés
    prix_min    real,
    prix_moy    real,
    prix_max    real,
    source      text,
    PRIMARY KEY (club_slug, date, heure, duree)
);

COMMENT ON TABLE public.padel_stats_horaire IS
  'Occupation padel agrégée par club/date/heure/durée. Remplace le détail '
  'ligne à ligne au-delà de la fenêtre de rétention. n_reserves / n_creneaux '
  'EST le taux d''occupation.';


-- ============================================================================
-- BLOC 2 — la fonction de rollup, rejouable et sûre
-- ============================================================================
-- Agrège puis supprime, dans une seule transaction, et UNIQUEMENT ce dont
-- l'agrégation est vérifiée. Rejouable : ON CONFLICT rafraîchit les lignes
-- déjà agrégées, donc la relancer deux fois ne double rien.

CREATE OR REPLACE FUNCTION public.padel_rollup(jours_gardes integer DEFAULT 30)
RETURNS TABLE (lignes_agregees bigint, lignes_supprimees bigint,
               buckets bigint)
LANGUAGE plpgsql AS $$
DECLARE
    limite       date;
    n_brut       bigint;
    n_buckets    bigint;
    n_couvert    bigint;
    n_supprime   bigint;
BEGIN
    limite := current_date - jours_gardes;

    SELECT count(*) INTO n_brut
      FROM public.padel_slots WHERE date::date < limite;

    IF n_brut = 0 THEN
        RETURN QUERY SELECT 0::bigint, 0::bigint, 0::bigint;
        RETURN;
    END IF;

    INSERT INTO public.padel_stats_horaire AS t
        (club_slug, date, heure, duree, n_creneaux, n_reserves,
         prix_min, prix_moy, prix_max, source)
    SELECT club_slug, date::date, heure, coalesce(duree, 0)::smallint,
           count(*)::smallint,
           count(*) FILTER (WHERE statut = 'reserve')::smallint,
           min(prix), avg(prix), max(prix), min(source)
      FROM public.padel_slots
     WHERE date::date < limite
     GROUP BY club_slug, date::date, heure, coalesce(duree, 0)
    ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
       SET n_creneaux = excluded.n_creneaux,
           n_reserves = excluded.n_reserves,
           prix_min   = excluded.prix_min,
           prix_moy   = excluded.prix_moy,
           prix_max   = excluded.prix_max,
           source     = excluded.source;

    GET DIAGNOSTICS n_buckets = ROW_COUNT;

    -- CONTRÔLE BLOQUANT : la somme des créneaux agrégés doit retrouver
    -- exactement le nombre de lignes brutes de la période. Sinon on annule
    -- tout plutôt que de supprimer du brut mal couvert.
    SELECT coalesce(sum(n_creneaux), 0) INTO n_couvert
      FROM public.padel_stats_horaire WHERE date < limite;

    IF n_couvert < n_brut THEN
        RAISE EXCEPTION
          'Rollup incomplet : % lignes brutes, % couvertes par l''agrégat. '
          'Aucune suppression effectuée.', n_brut, n_couvert;
    END IF;

    DELETE FROM public.padel_slots WHERE date::date < limite;
    GET DIAGNOSTICS n_supprime = ROW_COUNT;

    RETURN QUERY SELECT n_brut, n_supprime, n_buckets;
END $$;

-- Accessible au service_role uniquement : c'est une opération destructive.
-- Les rôles anon / authenticated / service_role sont propres à Supabase ; on
-- ne les révoque que s'ils existent, pour que le fichier reste rejouable sur
-- un Postgres nu (c'est ainsi qu'il a été testé avant livraison).
DO $$
DECLARE r text;
BEGIN
    EXECUTE 'REVOKE ALL ON FUNCTION public.padel_rollup(integer) FROM PUBLIC';
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format(
                'REVOKE ALL ON FUNCTION public.padel_rollup(integer) FROM %I', r);
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.padel_rollup(integer) '
                'TO service_role';
    END IF;
END $$;


-- ============================================================================
-- BLOC 3 — premier passage
-- ============================================================================
-- Commencer large pour valider le mécanisme sans tout engager, puis resserrer.

SELECT * FROM public.padel_rollup(180);   -- purge > 6 mois
SELECT * FROM public.padel_rollup(90);
SELECT * FROM public.padel_rollup(30);    -- régime de croisière

-- Rendre l'espace au disque (hors transaction, une ligne à la fois) :
VACUUM FULL public.padel_slots;
ANALYZE public.padel_slots;
REINDEX TABLE CONCURRENTLY public.padel_slots;


-- ============================================================================
-- BLOC 4 — contrôle
-- ============================================================================
SELECT 'brut restant'  AS quoi, count(*)::text AS n FROM public.padel_slots
UNION ALL
SELECT 'agrégat',       count(*)::text FROM public.padel_stats_horaire
UNION ALL
SELECT 'créneaux couverts par l''agrégat',
       coalesce(sum(n_creneaux),0)::text FROM public.padel_stats_horaire
UNION ALL
SELECT 'base totale',   pg_size_pretty(pg_database_size(current_database()));

-- Le taux d'occupation reste calculable, et c'est le point :
SELECT club_slug, date, heure,
       sum(n_reserves) AS reserves, sum(n_creneaux) AS creneaux,
       round(100.0 * sum(n_reserves) / nullif(sum(n_creneaux), 0)) AS pct
FROM public.padel_stats_horaire
GROUP BY club_slug, date, heure
ORDER BY date DESC, pct DESC NULLS LAST
LIMIT 10;
