#!/usr/bin/env python3
"""Belly Studio (bsport, 5 companies / 5 studios) — voir bsport_scrape.py.

Plateforme bsport. 5 studios = 5 companies distinctes :
  5424 -> BELLY STUDIO - PARIS 8e
  5458 -> BELLY STUDIO - PARIS 17e
  5466 -> BELLY STUDIO - BOULOGNE
  5675 -> BELLY STUDIO - PARIS 11e
  6049 -> BELLY STUDIO - PARIS 5e
Chaque company expose aussi une "Fake Room" (établissement technique sans
offre) que l'on ignore.
"""
import bsport_scrape
from bsport_scrape import run

# Patch : ignorer les établissements techniques "Fake Room" (pas de cours réels)
_orig_fetch_est = bsport_scrape.fetch_establishments


def fetch_establishments(company):
    names, skip = _orig_fetch_est(company)
    for eid, nm in list(names.items()):
        if "FAKE" in (nm or "").upper():
            skip.add(eid)
            del names[eid]
    return names, skip


bsport_scrape.fetch_establishments = fetch_establishments

CONFIG = {"key": "belly", "brand": "BELLY STUDIO",
          "companies": [5424, 5458, 5466, 5675, 6049],
          "host": "studiobelly.com", "store": "belly_data.json",
          "html": "belly.html", "csv": "belly_seances.csv", "price": 25,
          "accent": "#e0699b", "accent2": "#b07ff0",
          "methode": "<b>Belly Studio &middot; plateforme bsport (5 studios : Paris 5e, 8e, 11e, 17e &amp; Boulogne).</b> Présents = réservations confirmées (validated_booking_count) ; capacité = effectif. Fenêtre glissante d'environ 7 jours → l'historique s'accumule à chaque relevé (MAJ toutes les 5 min). Décompte <b>arrêté à la 1re séance à venir</b>. Bouton &laquo; Mettre à jour &raquo; = lecture en direct de l'API bsport."}

if __name__ == "__main__":
    run(CONFIG)
