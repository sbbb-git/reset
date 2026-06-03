# Migration JSON stores → Supabase

Le repo embarque actuellement **25 fichiers `*_data.json` trackés en git** pour
un total de ~130 MB. Les deux plus gros (`padel_national_data.json` ≈ 98 MB,
`padel_idf_data.json` ≈ 32 MB) représentent à eux seuls > 95 % de cette masse
et bloatent l'historique git à chaque scrape (≈ toutes les 2 h).

Ce document décrit la stratégie de migration **en trois phases** pour vider
ces stores du repo tout en gardant les HTMLs fonctionnels (zéro régression
côté front).

## État des lieux (top 5)

| Store                     | Taille | Consumers principaux                                                                                                                      | Sync Supabase  |
|---------------------------|--------|-------------------------------------------------------------------------------------------------------------------------------------------|----------------|
| `padel_national_data.json`| 98 MB  | `padel_national_scrape.py`, `prune_padel_live.py` (aucun HTML direct)                                                                     | **NEW** (cette PR) |
| `padel_idf_data.json`     | 32 MB  | `padel_club.html`, `padel_idf.html`, `padel_map.html`, `secteurs.html`, scripts compute/scrape (`padel_*_compute.py`, `*_scrape.py`, etc.)| déjà OK (`padel_supabase_sync.py`) |
| `thenewme_data.json`      | 960 KB | `thenewme_scrape.py` uniquement                                                                                                           | non urgent     |
| `dna_data.json`           | 471 KB | `dna_scrape.py` uniquement                                                                                                                | non urgent     |
| `banote_data.json`        | 396 KB | `banote_scrape.py` uniquement                                                                                                             | non urgent     |

Les 23 « petits » stores (< 1 MB) pèsent ensemble ~5 MB et sont laissés en
l'état pour l'instant (priorité aux deux mastodontes padel).

## Tables Supabase cibles

Schéma déjà existant (`padel_supabase_schema.sql`) :
- `padel_clubs(slug PK, source, unified_id, name, cp, city, lat, lng, metro, meta jsonb, updated_at)`
- `padel_slots(id PK, club_slug FK, date, heure, fin, duree, terrain, court_id, prix, statut, finie, source, premier_vu, dernier_vu, updated_at)`

La distinction **IDF vs reste de la France** est portée par la colonne
`metro` (ajoutée par ALTER dans cette PR) :
- `metro = 'idf'` → club d'Île-de-France (sync via `padel_supabase_sync.py`)
- `metro = '13'` / `'33'` / `'fr'` / … → reste FR (sync via `padel_national_supabase_sync.py`)

L'upsert est idempotent (`resolution=merge-duplicates` sur `slug` pour les
clubs, sur `id` pour les slots).

---

## Phase A — Push vers Supabase (cette PR)

**Objectif :** les données sont déjà en base, peu importe ce qu'il advient du JSON.

- `padel_national_supabase_sync.py` lit `padel_national_data.json` et upsert
  les `padel_clubs` (avec colonne `metro` calculée) + `padel_slots`.
- Le workflow `sectors-padel-national.yml` exécute ce script juste après
  le scrape, en `continue-on-error` (le manque de secrets ne casse pas le scrape).
- Idempotent : on peut le rejouer N fois sans dupliquer.

**Critère de succès :** après un run CI, `select count(*) from padel_clubs where metro != 'idf'` retourne ~700+ clubs et `padel_slots` reflète tout l'inventaire national.

## Phase B — Reconstruct JSON depuis Supabase, côté CI

**Objectif :** Supabase devient la source de vérité, le JSON local
n'est plus qu'un cache éphémère pour les consumers qui ne sont pas encore
migrés.

- `padel_national_from_supabase.py` paginé sur PostgREST, ne pull QUE les
  clubs `metro != 'idf'`, reconstruit le store et l'écrit sur disque.
- Le workflow tourne dans cet ordre : `scrape → sync Supabase → rebuild
  depuis Supabase → prune_padel_live.py (fenêtre 30 j passé / 60 j futur)
  → commit`.
- Résultat attendu sur git : `padel_national_data.json` commité reste petit
  (~5 MB après prune) et le diff git par run reste raisonnable.

**Critère de succès :** la taille du fichier commité chute de 98 MB → ~5 MB,
les diffs git deviennent lisibles, l'historique cesse de gonfler.

## Phase C — Refactor HTMLs pour fetch direct Supabase

**Objectif :** dégager définitivement les JSONs du repo.

Plus aucun HTML ne lit `padel_national_data.json` aujourd'hui (vérifié par
grep) — la migration est donc triviale côté national. Côté IDF, plusieurs
HTMLs lisent `padel_idf_data.json` ; il faudra appliquer le même pattern.

### Exemple de refactor (avant / après)

```html
<!-- AVANT : fetch fichier statique tracké en git -->
<script>
fetch('padel_idf_data.json')
  .then(r => r.json())
  .then(store => render(store));
</script>
```

```html
<!-- APRES : fetch direct PostgREST (anon key, RLS read-only) -->
<script>
const SUPABASE_URL = 'https://xxxxx.supabase.co';
const SUPABASE_ANON = 'eyJhbGciOi...';  // clé anon (lecture seule via RLS)

async function loadPadelIdf() {
  const headers = {
    apikey: SUPABASE_ANON,
    Authorization: `Bearer ${SUPABASE_ANON}`,
  };

  // 1. Clubs IDF (≈ 200 lignes, une seule requête)
  const clubs = await fetch(
    `${SUPABASE_URL}/rest/v1/padel_clubs?metro=eq.idf&select=*`,
    { headers }).then(r => r.json());

  // 2. Slots (pagination par range pour rester sous la limite PostgREST)
  const slots = [];
  let offset = 0, page = 1000;
  while (true) {
    const chunk = await fetch(
      `${SUPABASE_URL}/rest/v1/padel_slots?select=*&order=date.asc` +
      `&limit=${page}&offset=${offset}`,
      { headers }).then(r => r.json());
    slots.push(...chunk);
    if (chunk.length < page) break;
    offset += page;
  }

  // 3. Re-grouper en {slug: {meta, sessions: {sid: session}}} si nécessaire
  const store = {};
  for (const c of clubs) store[c.slug] = { meta: c, sessions: {} };
  for (const s of slots) {
    const slug = s.club_slug;
    if (!store[slug]) continue;
    const sid = s.id.includes('|') ? s.id.split('|')[1] : s.id;
    store[slug].sessions[sid] = s;
  }
  return store;
}

loadPadelIdf().then(render);
</script>
```

### Alternative : vue agrégée (lecture KPI uniquement)

Pour les dashboards qui n'ont pas besoin des slots individuels, la vue
`padel_clubs_kpi` (déjà définie dans le schéma) suffit et tient en une seule
requête :

```js
fetch(`${SUPABASE_URL}/rest/v1/padel_clubs_kpi?metro=eq.idf&select=*`,
      { headers });
```

### Une fois tous les HTML migrés

- supprimer `padel_*_data.json` du tracking git (`git rm --cached`)
- ajouter une entrée `*_data.json` au `.gitignore`
- supprimer `padel_national_from_supabase.py` (devenu inutile) et la step
  « Reconstruct » du workflow
- éventuellement supprimer `prune_padel_live.py`

---

## Récap : ce que livre cette PR

- `padel_national_supabase_sync.py` (nouveau) — phase A
- `padel_national_from_supabase.py` (nouveau) — phase B
- `padel_supabase_schema.sql` — ajout colonne `metro` + index
- `.github/workflows/sectors-padel-national.yml` — pipeline scrape → sync
  → rebuild → prune → commit
- `MIGRATION_PLAN.md` — ce document

**Aucun JSON existant n'est supprimé** : la transition est progressive et
réversible. Les phases B et C peuvent se déployer indépendamment sur
chaque store concerné.
