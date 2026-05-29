#!/usr/bin/env python3
"""The New Me (bsport company 4272) — voir bsport_scrape.py."""
from bsport_scrape import run
CONFIG = {"key":"thenewme","brand":"THE NEW ME","company":4272,
          "host":"thenewmeparis.com","store":"thenewme_data.json",
          "html":"thenewme.html","csv":"thenewme_seances.csv","price":20,
          "accent":"#b8895e","accent2":"#d8b08a"}
if __name__ == "__main__":
    run(CONFIG)
