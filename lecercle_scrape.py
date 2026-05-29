#!/usr/bin/env python3
"""Le Cercle Boxing (bsport company 2443) — voir bsport_scrape.py."""
from bsport_scrape import run
CONFIG = {"key":"lecercle","brand":"LE CERCLE BOXING","company":2443,
          "host":"lecercle-boxing.com","store":"lecercle_data.json",
          "html":"lecercle.html","csv":"lecercle_seances.csv","price":20,
          "accent":"#d4453a","accent2":"#ef8079",
          "methode":"<b>Le Cercle Boxing &middot; plateforme bsport.</b> Présents = réservations confirmées ; capacité = effectif. Fenêtre glissante d'environ 7 jours → historique accumulé à chaque relevé (MAJ toutes les 5 min). Décompte <b>arrêté à la 1re séance à venir</b>. Bouton &laquo; Mettre à jour &raquo; = lecture en direct de l'API bsport."}
if __name__ == "__main__":
    run(CONFIG)
