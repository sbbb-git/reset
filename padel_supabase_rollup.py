#!/usr/bin/env python3
"""Appelle padel_rollup() dans Supabase : agrège l'historique padel, purge le brut.

La fonction SQL fait tout le travail côté serveur (agrégation + contrôle
bloquant + suppression dans une seule transaction). Ce script ne fait que la
déclencher depuis la CI, qui détient la service_key.

Pourquoi côté serveur : agréger 1,4 M de lignes en les rapatriant coûterait
plusieurs minutes et autant de bande passante, pour un GROUP BY que Postgres
fait en quelques secondes.

Sécurité : padel_rollup() refuse de supprimer si la somme des créneaux agrégés
ne retrouve pas le nombre de lignes brutes de la période. En cas d'écart elle
lève une exception et la transaction est annulée — rien n'est perdu.

Usage : JOURS_GARDES=30 python3 padel_supabase_rollup.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
JOURS = int(os.environ.get("JOURS_GARDES", "30"))


def main():
    if not URL or not KEY:
        print("❌ SUPABASE_URL / SUPABASE_SERVICE_KEY non définis", file=sys.stderr)
        sys.exit(1)

    body = json.dumps({"jours_gardes": JOURS}).encode("utf-8")
    req = urllib.request.Request(
        f"{URL}/rest/v1/rpc/padel_rollup", data=body,
        headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            res = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:500]
        if e.code == 404:
            print("::warning::padel_rollup() absente — jouer d'abord "
                  "supabase_levier3_rollup.sql. Rien n'a été modifié.",
                  file=sys.stderr)
            return
        print(f"::error::rollup HTTP {e.code} : {detail}", file=sys.stderr)
        sys.exit(1)

    ligne = res[0] if isinstance(res, list) and res else (res or {})
    agr = ligne.get("lignes_agregees", 0)
    sup = ligne.get("lignes_supprimees", 0)
    buc = ligne.get("buckets", 0)
    if not agr:
        print(f"Rien à agréger au-delà de {JOURS} jours.")
        return
    print(f"Rollup padel : {agr:,} lignes brutes → {buc:,} lignes d'agrégat, "
          f"{sup:,} supprimées (fenêtre gardée : {JOURS} jours)")
    if sup != agr:
        print(f"::warning::{agr - sup} lignes agrégées mais non supprimées",
              file=sys.stderr)


if __name__ == "__main__":
    main()
