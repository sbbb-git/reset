-- ============================================================================
-- PURGE DES DOUBLONS DE SESSIONS — le33foch, banote, dna
-- ============================================================================
-- Les stores de ces trois marques contenaient jusqu'à 98 % de doublons (id
-- HTML Mindbody renouvelé à chaque affichage, cf. seance_cle.py). Les stores
-- ont été fusionnés le 06/10 ; la table `sessions` porte encore les anciennes
-- lignes, sous les anciens source_id.
--
-- On vide ces trois marques, puis supabase-sync.yml (fenetre_jours=0) les
-- réinjecte depuis les stores propres. Les autres marques ne sont pas
-- touchées. Les stores étant dans git, rien n'est perdu.
--
-- Découpé par marque et par mois : un DELETE de ~184 000 lignes d'un bloc
-- dépasserait le délai d'exécution de l'API.
-- ============================================================================

SELECT brand_key, count(*) AS lignes_avant
FROM public.sessions
WHERE brand_key IN ('le33foch', 'banote', 'dna')
GROUP BY brand_key ORDER BY brand_key;

-- le33foch
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date < '2026-01-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2026-01-01' AND date < '2026-02-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2026-02-01' AND date < '2026-03-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2026-03-01' AND date < '2026-04-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2026-04-01' AND date < '2026-05-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2026-05-01' AND date < '2026-06-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2026-06-01' AND date < '2026-07-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2026-07-01' AND date < '2026-08-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2026-08-01' AND date < '2026-09-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2026-09-01' AND date < '2026-10-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2026-10-01' AND date < '2026-11-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2026-11-01' AND date < '2026-12-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2026-12-01' AND date < '2027-01-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2027-01-01' AND date < '2027-02-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2027-02-01' AND date < '2027-03-01';
DELETE FROM public.sessions WHERE brand_key = 'le33foch' AND date >= '2027-03-01';

-- banote
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date < '2026-01-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2026-01-01' AND date < '2026-02-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2026-02-01' AND date < '2026-03-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2026-03-01' AND date < '2026-04-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2026-04-01' AND date < '2026-05-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2026-05-01' AND date < '2026-06-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2026-06-01' AND date < '2026-07-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2026-07-01' AND date < '2026-08-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2026-08-01' AND date < '2026-09-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2026-09-01' AND date < '2026-10-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2026-10-01' AND date < '2026-11-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2026-11-01' AND date < '2026-12-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2026-12-01' AND date < '2027-01-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2027-01-01' AND date < '2027-02-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2027-02-01' AND date < '2027-03-01';
DELETE FROM public.sessions WHERE brand_key = 'banote' AND date >= '2027-03-01';

-- dna
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date < '2026-01-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2026-01-01' AND date < '2026-02-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2026-02-01' AND date < '2026-03-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2026-03-01' AND date < '2026-04-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2026-04-01' AND date < '2026-05-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2026-05-01' AND date < '2026-06-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2026-06-01' AND date < '2026-07-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2026-07-01' AND date < '2026-08-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2026-08-01' AND date < '2026-09-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2026-09-01' AND date < '2026-10-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2026-10-01' AND date < '2026-11-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2026-11-01' AND date < '2026-12-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2026-12-01' AND date < '2027-01-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2027-01-01' AND date < '2027-02-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2027-02-01' AND date < '2027-03-01';
DELETE FROM public.sessions WHERE brand_key = 'dna' AND date >= '2027-03-01';

-- Contrôle : doit renvoyer 0 ligne.
SELECT brand_key, count(*) AS restantes
FROM public.sessions
WHERE brand_key IN ('le33foch', 'banote', 'dna')
GROUP BY brand_key;
