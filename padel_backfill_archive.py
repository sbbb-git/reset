#!/usr/bin/env python3
"""Réinjecte dans Supabase les créneaux padel archivés pendant une panne.

POURQUOI
Du 24/09 au 06/10, toutes les écritures Supabase ont échoué en 401 (clé de
service périmée). Pendant ce temps, le store padel continuait de vivre :
chaque jour, les créneaux sortis de la fenêtre [J-7 ; J+14] partaient dans
l'archive .gz — sans jamais avoir atteint la base dans leur état final
(réservé, terminé…). Le sync normal, une fois réparé, ne voit que le store
vivant : il ne peut pas rattraper ces créneaux-là.

COMMENT
On reconstitue un store temporaire avec les seuls créneaux archivés datés de
[BACKFILL_DEBUT ; BACKFILL_FIN], et on le passe au sync habituel. Le sync
étant incrémental, l'empreinte `_sync_h` conservée dans l'archive décide de
ce qui part : un créneau dont l'état final avait déjà été envoyé avant la
panne est sauté, les autres partent. L'upsert sur la clé métier rend le tout
idempotent : relancer ne double rien.

Les fiches club viennent du STORE VIVANT quand il les connaît : celles de
l'archive peuvent être plus anciennes (coordonnées ajoutées depuis, par
exemple), et un upsert les aurait écrasées par du vide.

À lancer APRÈS qu'un sync normal a réussi (les clubs récents doivent exister).

Usage : BACKFILL_DEBUT=2026-09-15 BACKFILL_FIN=2026-09-28 python3 padel_backfill_archive.py
        (FIN par défaut : J-8, la veille de la fenêtre vivante ;
         BACKFILL_PERIMETRE = idf (défaut) | national | tous)
"""
import datetime as dt
import gzip
import importlib
import json
import os
import sys
import tempfile

SOURCES = {
    # périmètre: (archive,                   store vivant,               module de sync)
    "idf":      ("padel_idf_history.json.gz",      "padel_idf_data.json",      "padel_supabase_sync"),
    "national": ("padel_national_history.json.gz", "padel_national_data.json", "padel_national_supabase_sync"),
}


def _charger(chemin, gz=False):
    if not os.path.exists(chemin):
        return {}
    ouvrir = gzip.open if gz else open
    with ouvrir(chemin, "rt", encoding="utf-8") as f:
        return json.load(f)


def main():
    debut = os.environ.get("BACKFILL_DEBUT", "")
    fin = os.environ.get("BACKFILL_FIN") or (
        dt.date.today() - dt.timedelta(days=8)).isoformat()
    if not debut:
        print("::error::BACKFILL_DEBUT requis (AAAA-MM-JJ)")
        sys.exit(1)
    # IDF par défaut : le national n'a jamais été en base avant le 07/10, et
    # ~100 000 créneaux d'historique hors IDF pèseraient ~40 Mo sur un quota
    # de 500 Mo pour un intérêt faible. BACKFILL_PERIMETRE=tous pour les deux.
    perimetre = (os.environ.get("BACKFILL_PERIMETRE") or "idf").strip().lower()
    choix = list(SOURCES) if perimetre == "tous" else [perimetre]
    if any(c not in SOURCES for c in choix):
        print(f"::error::BACKFILL_PERIMETRE inconnu : {perimetre} (idf | national | tous)")
        sys.exit(1)
    print(f"Rattrapage padel ({', '.join(choix)}) : créneaux archivés du {debut} au {fin}\n")

    echecs = 0
    for archive, vivant, module in (SOURCES[c] for c in choix):
        arch = _charger(archive, gz=True)
        live = _charger(vivant)
        sous_store, n = {}, 0
        for slug, club in arch.items():
            sel = {sid: s for sid, s in (club.get("sessions") or {}).items()
                   if debut <= ((s or {}).get("date") or "") <= fin}
            if not sel:
                continue
            meta = (live.get(slug) or {}).get("meta") or club.get("meta") or {}
            sous_store[slug] = {"meta": meta, "sessions": sel}
            n += len(sel)
        print(f"=== {archive} : {n:,} créneaux dans la plage, "
              f"{len(sous_store)} clubs")
        if not n:
            continue

        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sous_store, f, ensure_ascii=False)
        m = importlib.import_module(module)
        m.STORE = tmp                      # le sync lit ce fichier-là
        try:
            m.main()
        except SystemExit as e:
            if e.code:
                echecs += 1
        except Exception as e:  # noqa: BLE001
            print(f"::error::{module} : {type(e).__name__} : {e}")
            echecs += 1
        finally:
            os.unlink(tmp)
        print()

    if echecs:
        print(f"::error::{echecs} source(s) en échec")
        sys.exit(1)
    print("Rattrapage terminé.")


if __name__ == "__main__":
    main()
