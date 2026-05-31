#!/usr/bin/env python3
"""DRIP HIIT (bsport company 2441) — voir bsport_scrape.py.

Studio HIIT / Bootcamp Grands Boulevards (29 rue des Petites Écuries 75010).
Membre du Sanctuary Group.
"""
from bsport_scrape import run
CONFIG = {"key":"driphiit","brand":"DRIP HIIT","company":2441,
          "host":"drip-hiit.com","store":"driphiit_data.json",
          "html":"driphiit.html","csv":"driphiit_seances.csv","price":29,
          "accent":"#0bbfae","accent2":"#5fe0d2",
          "methode":"<b>DRIP HIIT &middot; plateforme bsport.</b> Présents = réservations confirmées ; capacité = effectif. Fenêtre glissante d'environ 7 jours → historique accumulé à chaque relevé (MAJ toutes les 5 min). Décompte <b>arrêté à la 1re séance à venir</b>. Bouton &laquo; Mettre à jour &raquo; = lecture en direct de l'API bsport."}
if __name__ == "__main__":
    run(CONFIG)
