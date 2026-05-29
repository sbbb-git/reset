#!/usr/bin/env python3
"""The New Me (bsport, multi-companies) — voir bsport_scrape.py."""
from bsport_scrape import run
CONFIG = {"key":"thenewme","brand":"THE NEW ME",
          "companies":[4272,4273,4275,4276,4277,4278,4279,4283,4284,4285,4287,
                       4902,4903,4904,4905,4906,4907,4908,4909,4913,4915,4917,
                       4919,4920,4921,4986,5699],
          "host":"thenewmeparis.com","store":"thenewme_data.json",
          "html":"thenewme.html","csv":"thenewme_seances.csv","price":20,
          "accent":"#b8895e","accent2":"#d8b08a",
          "methode":"<b>The New Me &middot; plateforme bsport (27 companies, 45 studios &mdash; France + Bruxelles).</b> Présents = réservations confirmées ; capacité = effectif. La plateforme n'expose qu'une fenêtre glissante d'environ 7 jours → l'historique s'accumule à chaque relevé (MAJ toutes les 5 min). Décompte <b>arrêté à la 1re séance à venir</b>. Limites : pas de distinction no-show ; studios &laquo; coming soon &raquo; sans widget non listés. Bouton &laquo; Mettre à jour &raquo; = lecture en direct de l'API bsport."}
if __name__ == "__main__":
    run(CONFIG)
