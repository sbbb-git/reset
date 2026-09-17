-- ============================================================================
-- ROLLUP PAR JOUR — version qui tient dans le délai d'exécution
-- ============================================================================
--
-- POURQUOI CETTE SECONDE VERSION
-- La première traitait « tout ce qui a plus de N jours » en une fois. Sur ta
-- base elle a expiré, sur son tout premier comptage :
--   ERROR: 57014: canceling statement due to statement timeout
--   CONTEXT: SELECT count(*) FROM padel_slots WHERE date::date < limite
--
-- Le diagnostic a montré que le plan était pourtant bon :
--   Index Only Scan using padel_slots_date_idx (cost=0.43..17135.21)
--     Index Cond: (date < (CURRENT_DATE - 90))
-- et que `date` est déjà de type date — le ::date était un no-op inoffensif.
--
-- La lenteur vient donc du ballonnement : la visibility map est périmée après
-- des millions de réécritures quotidiennes, l'« Index Only Scan » doit aller
-- lire le tas pour chaque entrée, en accès aléatoire sur 206 MB, sur une
-- instance mutualisée. Un balayage large ne peut pas tenir.
--
-- D'où le découpage : une journée par appel, ~7 700 lignes, servies par
-- l'index. Chaque instruction reste courte quel que soit l'état du tas, et le
-- traitement devient REPRENABLE — s'il s'interrompt, ce qui est déjà replié
-- l'est, et on continue au jour suivant au lieu de tout rejouer.
--
-- Les garanties de la première version sont conservées : agrégation d'abord,
-- vérification que la somme des créneaux agrégés retrouve le nombre de lignes
-- brutes du jour, suppression seulement ensuite, le tout dans une transaction.
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
    SELECT club_slug, date, heure, coalesce(duree, 0)::smallint,
           least(count(*), 32767)::smallint,
           least(count(*) FILTER (WHERE statut = 'reserve'), 32767)::smallint,
           min(prix), avg(prix), max(prix), min(source)
      FROM public.padel_slots
     WHERE date = jour
     GROUP BY club_slug, date, heure, coalesce(duree, 0)
    ON CONFLICT (club_slug, date, heure, duree) DO UPDATE
       SET n_creneaux = excluded.n_creneaux,
           n_reserves = excluded.n_reserves,
           prix_min   = excluded.prix_min,
           prix_moy   = excluded.prix_moy,
           prix_max   = excluded.prix_max,
           source     = excluded.source;

    GET DIAGNOSTICS n_buckets = ROW_COUNT;

    -- Contrôle bloquant, à l'échelle du jour traité.
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

DO $$
DECLARE r text;
BEGIN
    EXECUTE 'REVOKE ALL ON FUNCTION public.padel_rollup_jour(date) FROM PUBLIC';
    FOREACH r IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format(
                'REVOKE ALL ON FUNCTION public.padel_rollup_jour(date) FROM %I', r);
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION public.padel_rollup_jour(date) '
                'TO service_role';
    END IF;
END $$;

-- Bornes, servies par padel_slots_date_idx : instantané.
SELECT min(date) AS plus_ancienne, max(date) AS plus_recente
FROM public.padel_slots;
