#!/usr/bin/env python3
"""Déroule le rollup padel jour par jour, via l'API Management.

POURQUOI UN PILOTE CÔTÉ CLIENT
padel_rollup_jour() ne traite qu'une journée, pour que chaque instruction
reste courte — la première version, qui repliait tout d'un coup, expirait sur
son comptage initial à cause du ballonnement de la table. C'est donc ici qu'on
boucle, un appel HTTP par jour.

Avantage secondaire, et il compte : le traitement est REPRENABLE. Si le run
est coupé, ce qui est déjà replié l'est ; le suivant repart du plus ancien
jour restant, sans rien rejouer.

Usage : JOURS_GARDES=30 python3 supabase_rollup_run.py
"""
import datetime as dt
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supabase_exec_sql import executer, project_ref  # noqa: E402

JOURS = int(os.environ.get("JOURS_GARDES", "30"))
MAX_JOURS_PAR_RUN = int(os.environ.get("MAX_JOURS_PAR_RUN", "400"))


def scalaire(ref, sql, champ):
    res = executer(ref, sql)
    if isinstance(res, list) and res:
        return res[0].get(champ)
    return None


def main():
    if not os.environ.get("SUPABASE_ACCESS_TOKEN"):
        print("::error::SUPABASE_ACCESS_TOKEN absent", file=sys.stderr)
        sys.exit(1)
    ref = project_ref()
    limite = dt.date.today() - dt.timedelta(days=JOURS)
    print(f"Rollup padel : on replie tout ce qui est antérieur au {limite} "
          f"(fenêtre gardée : {JOURS} jours)\n")

    total_lignes = total_buckets = jours_traites = 0
    for _ in range(MAX_JOURS_PAR_RUN):
        plus_ancienne = scalaire(
            ref, "SELECT min(date) AS d FROM public.padel_slots;", "d")
        if not plus_ancienne:
            print("Table vide — rien à faire.")
            break
        jour = dt.date.fromisoformat(str(plus_ancienne)[:10])
        if jour >= limite:
            print(f"Plus ancienne date restante : {jour} — dans la fenêtre, "
                  f"terminé.")
            break
        try:
            res = executer(
                ref, f"SELECT * FROM public.padel_rollup_jour('{jour}');")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:400]
            print(f"::error::échec sur le {jour} : HTTP {e.code} {detail}",
                  file=sys.stderr)
            print(f"\nInterrompu après {jours_traites} jours repliés "
                  f"({total_lignes:,} lignes). Ce qui est fait est acquis : "
                  f"relancer reprendra au {jour}.", file=sys.stderr)
            sys.exit(1)

        ligne = res[0] if isinstance(res, list) and res else {}
        n = ligne.get("lignes") or 0
        b = ligne.get("buckets") or 0
        total_lignes += n
        total_buckets += b
        jours_traites += 1
        print(f"  {jour} : {n:>6,} lignes → {b:>5,} agrégats")

        if n == 0:
            # Jour vide : sans suppression, min(date) ne bougerait pas et on
            # boucherait à l'infini. Ne devrait pas arriver puisque la date
            # vient de min(date), mais on préfère s'arrêter que tourner.
            print("::warning::jour sans ligne alors qu'il est le plus ancien — "
                  "arrêt pour éviter une boucle", file=sys.stderr)
            break
    else:
        print(f"::warning::limite de {MAX_JOURS_PAR_RUN} jours atteinte — "
              f"relancer pour continuer", file=sys.stderr)

    print(f"\n{jours_traites} jours repliés, {total_lignes:,} lignes brutes → "
          f"{total_buckets:,} lignes d'agrégat")

    for libelle, sql in (
            ("brut restant", "SELECT count(*) AS n FROM public.padel_slots "
                             "WHERE date < current_date - %d;" % JOURS),
            ("base totale", "SELECT pg_size_pretty("
                            "pg_database_size(current_database())) AS n;")):
        try:
            print(f"  {libelle} : {scalaire(ref, sql, 'n')}")
        except Exception as e:  # noqa: BLE001
            print(f"  {libelle} : indisponible ({type(e).__name__})")


if __name__ == "__main__":
    main()
