#!/usr/bin/env python3
"""Space Cycle (bsport company 2440) — voir bsport_scrape.py."""
from bsport_scrape import run
CONFIG = {"key":"spacecycle","brand":"SPACE CYCLE","company":2440,
          "host":"space-cycle.com","store":"spacecycle_data.json",
          "html":"spacecycle.html","csv":"spacecycle_seances.csv","price":20,
          "accent":"#16b3c6","accent2":"#5fd6e2"}
if __name__ == "__main__":
    run(CONFIG)
