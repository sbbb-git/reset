#!/usr/bin/env python3
"""Archive puis élague les stores padel vivants.

POURQUOI CE FICHIER A CHANGÉ (2026-08-08)
-----------------------------------------
L'ancienne version se contentait de SUPPRIMER les sessions hors fenêtre, en
supposant qu'elles étaient « déjà dans Supabase et dans le .gz d'archive ».
La première moitié est vraie ; la seconde ne l'était pas : aucun script
n'écrivait jamais dans padel_idf_history.json.gz (c'est un export figé de
doinsport_backfill.py), et aucune archive nationale n'existait. Élaguer
revenait donc à retirer définitivement ces créneaux de tout ce qui tourne en
local — les trois computes (kpis / insights / anomalies) lisent le store
vivant + le .gz, jamais Supabase.

Désormais : on ARCHIVE d'abord, on ne retire qu'après avoir relu l'archive
depuis le disque et vérifié que chaque session sortante s'y trouve. Si
l'archivage échoue, le store vivant n'est pas touché.

POURQUOI LA FENÊTRE RÉTRÉCIT (30/60 → 7/14)
-------------------------------------------
padel_idf_data.json pesait 99,89 MB — GitHub refuse tout fichier > 100 MB.
Chaque run ajoutait ses créneaux, le push partait en « pre-receive hook
declined », et la boucle de retry se terminant sur `sleep` (exit 0), le
workflow restait vert. Résultat : le store n'a pas bougé depuis le
2026-07-27 alors que sectors-padel.yml tourne toutes les 30 min.
padel_national_data.json est à 86,9 MB, sur la même trajectoire.

Les 4 engines de scrape (anybuddy/idf, urbanpadel, doinsport, playtomic —
le national les réutilise) déclarent tous HORIZON_JOURS = 7. Garder 14 jours
de futur laisse le double de l'horizon : aucun créneau encore observable
n'est retiré, donc la détection de réservation par disparition reste
intacte. 7 jours de passé laissent le temps à `finie` de se figer et à un
run de sync raté d'être rattrapé.

Effet mesuré au 2026-08-08 : IDF 99,9 MB → ~4 MB, national 86,9 MB → ~20 MB.

Ordre dans le workflow : scrapers → computes → sync Supabase → CE SCRIPT →
commit. Les computes voient donc toujours la fenêtre large avant élagage.
"""
import datetime as dt
import gzip
import json
import os
import sys
import tempfile

import safestore

WINDOW_DAYS = 7           # passé conservé dans le store vivant
WINDOW_FUTURE_DAYS = 14   # futur conservé — 2× l'horizon de scrape (7j)

# store vivant -> archive froide. Même structure des deux côtés :
# {slug: {"meta": {...}, "sessions": {sid: {...}}}}
STORES = {
    "padel_idf_data.json": "padel_idf_history.json.gz",
    "padel_national_data.json": "padel_national_history.json.gz",
}


def load_archive(path):
    if not os.path.exists(path):
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        data = json.load(f)  # JSONDecodeError non rattrapé : on abandonne
    if not isinstance(data, dict):
        raise ValueError(f"{path} : format inattendu (pas un dict)")
    return data


def save_archive(archive, path):
    """Écriture atomique (tmp + os.replace), comme safestore mais gzippée."""
    folder = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".tmp_", suffix=".gz")
    os.close(fd)
    try:
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(archive, f, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _derniere_vue(s):
    return (s or {}).get("dernier_vu") or ""


def prune_one(store_path, archive_path):
    if not os.path.exists(store_path):
        print(f"  skip {store_path} (absent)")
        return
    size_before = os.path.getsize(store_path)
    store = safestore.load(store_path)

    today = dt.date.today()
    min_date = (today - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    max_date = (today + dt.timedelta(days=WINDOW_FUTURE_DAYS)).isoformat()

    # 1. Repérer les sortants, sans rien retirer encore.
    sortants = {}
    n_before = 0
    for slug, club in store.items():
        sessions = club.get("sessions")
        if not isinstance(sessions, dict):
            continue
        n_before += len(sessions)
        out = {sid: s for sid, s in sessions.items()
               if not (min_date <= ((s or {}).get("date") or "") <= max_date)}
        if out:
            sortants[slug] = out

    n_sortants = sum(len(v) for v in sortants.values())
    if not n_sortants:
        print(f"  ✓ {store_path} : {n_before:,} sessions, rien hors "
              f"[{min_date} ; {max_date}]")
        return

    # 2. Archiver AVANT de retirer.
    archive = load_archive(archive_path)
    ajouts = maj = 0
    for slug, out in sortants.items():
        dest = archive.setdefault(
            slug, {"meta": (store[slug].get("meta") or {}), "sessions": {}})
        if not isinstance(dest.get("sessions"), dict):
            dest["sessions"] = {}
        for sid, s in out.items():
            old = dest["sessions"].get(sid)
            if old is None:
                dest["sessions"][sid] = s
                ajouts += 1
            elif _derniere_vue(s) > _derniere_vue(old):
                # Le store vivant a vu ce créneau plus récemment que l'archive :
                # son `statut`/`finie` est le plus abouti des deux.
                dest["sessions"][sid] = s
                maj += 1
    if ajouts or maj:
        save_archive(archive, archive_path)

    # 3. Relire l'archive DEPUIS LE DISQUE. On ne retire du store vivant que ce
    #    qu'on a vérifié comme réellement écrit — sinon on n'élague pas.
    relu = load_archive(archive_path)
    manquants = 0
    for slug, out in sortants.items():
        arch = (relu.get(slug) or {}).get("sessions") or {}
        manquants += sum(1 for sid in out if sid not in arch)
    if manquants:
        print(f"  ❌ {store_path} : {manquants:,} sessions absentes de "
              f"{archive_path} après écriture — élagage ANNULÉ, rien n'est "
              f"perdu, le store vivant reste complet.", file=sys.stderr)
        return

    # 4. Maintenant seulement, retirer du store vivant.
    n_after = 0
    for slug, club in store.items():
        sessions = club.get("sessions")
        if not isinstance(sessions, dict):
            continue
        out = sortants.get(slug)
        if out:
            club["sessions"] = {sid: s for sid, s in sessions.items()
                                if sid not in out}
        n_after += len(club["sessions"])
    safestore.save(store, store_path)

    size_after = os.path.getsize(store_path)
    pct = 100 * (1 - size_after / size_before) if size_before else 0
    n_arch = sum(len(c.get("sessions") or {}) for c in relu.values())
    print(f"  ✂️  {store_path} : {n_before:,} → {n_after:,} sessions "
          f"({size_before/1e6:.1f} MB → {size_after/1e6:.1f} MB, -{pct:.0f}%)")
    print(f"     ↳ {archive_path} : +{ajouts:,} archivées, {maj:,} rafraîchies, "
          f"{n_arch:,} au total ({os.path.getsize(archive_path)/1e6:.1f} MB)")


def main():
    print(f"Élagage padel : fenêtre = [today-{WINDOW_DAYS}j ; "
          f"today+{WINDOW_FUTURE_DAYS}j], archivage préalable obligatoire")
    for store_path, archive_path in STORES.items():
        prune_one(store_path, archive_path)


if __name__ == "__main__":
    main()
