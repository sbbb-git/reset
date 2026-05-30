#!/usr/bin/env python3
"""AthletX Rebel (bsport, 2 companies / 2 lieux) — voir bsport_scrape.py.

Plateforme bsport. 2 lieux = 2 companies distinctes :
  5434 -> AthletX Rebel Paris République
  1641 -> AthletX Rebel Neuilly-sur-Seine
Chaque company expose plusieurs établissements internes correspondant aux
salles spécialisées (Bootcamp, Reformer, Hyrox, Pilates, X-Bike, Yoga, …).
"""
import bsport_scrape
from bsport_scrape import run

# Les 2 companies (Paris République et Neuilly) ont des établissements aux noms
# génériques identiques ("STUDIO REFORMER", "STUDIO BOOTCAMP"...) qu'il faut
# qualifier avec le nom du club pour les distinguer dans le dashboard.
COMPANY_LABEL = {5434: "Paris République", 1641: "Neuilly"}
_orig_fetch_est = bsport_scrape.fetch_establishments


def fetch_establishments(company):
    names, skip = _orig_fetch_est(company)
    label = COMPANY_LABEL.get(company, str(company))
    out = {}
    for eid, nm in names.items():
        nm = (nm or "").strip()
        # Si le nom contient déjà République/Neuilly on garde tel quel
        if any(tag in nm.upper() for tag in ("REPUBLIQUE", "RÉPUBLIQUE", "NEUILLY")):
            out[eid] = nm
        else:
            out[eid] = f"{nm} - {label}"
    return out, skip


bsport_scrape.fetch_establishments = fetch_establishments

CONFIG = {
    "key": "athletx", "brand": "ATHLETX",
    "companies": [5434, 1641],
    "host": "athletxrebel.com", "store": "athletx_data.json",
    "html": "athletx.html", "csv": "athletx_seances.csv", "price": 25,
    "accent": "#e0322c", "accent2": "#ff6900",
    "methode": "<b>AthletX Rebel &middot; plateforme bsport (2 clubs : Paris République &amp; Neuilly-sur-Seine, plusieurs salles spécialisées par club).</b> Présents = réservations confirmées (validated_booking_count) ; capacité = effectif. Fenêtre glissante d'environ 7 jours → l'historique s'accumule à chaque relevé (MAJ toutes les 5 min). Décompte <b>arrêté à la 1re séance à venir</b>. Bouton &laquo; Mettre à jour &raquo; = lecture en direct de l'API bsport.",
}

if __name__ == "__main__":
    run(CONFIG)
