#!/usr/bin/env python3
"""Le Cercle Boxing (bsport company 2443) — voir bsport_scrape.py."""
from bsport_scrape import run
CONFIG = {"key":"lecercle","brand":"LE CERCLE BOXING","company":2443,
          "host":"lecercle-boxing.com","store":"lecercle_data.json",
          "html":"lecercle.html","csv":"lecercle_seances.csv","price":20,
          "accent":"#d4453a","accent2":"#ef8079"}
if __name__ == "__main__":
    run(CONFIG)
