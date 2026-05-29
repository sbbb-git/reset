#!/usr/bin/env python3
"""Poses Studio (bsport company 2442) — voir bsport_scrape.py."""
from bsport_scrape import run
CONFIG = {"key":"poses","brand":"POSES STUDIO","company":2442,
          "host":"poses-studio.com","store":"poses_data.json",
          "html":"poses.html","csv":"poses_seances.csv","price":20,
          "accent":"#b06a8f","accent2":"#d49ab5",
          "methode":"<b>Poses Studio &middot; plateforme bsport.</b> Présents = réservations confirmées ; capacité = effectif. Fenêtre glissante d'environ 7 jours → historique accumulé à chaque relevé (MAJ toutes les 5 min). Décompte <b>arrêté à la 1re séance à venir</b>. Bouton &laquo; Mettre à jour &raquo; = lecture en direct de l'API bsport."}
if __name__ == "__main__":
    run(CONFIG)
