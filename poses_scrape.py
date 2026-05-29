#!/usr/bin/env python3
"""Poses Studio (bsport company 2442) — voir bsport_scrape.py."""
from bsport_scrape import run
CONFIG = {"key":"poses","brand":"POSES STUDIO","company":2442,
          "host":"poses-studio.com","store":"poses_data.json",
          "html":"poses.html","csv":"poses_seances.csv","price":20,
          "accent":"#b06a8f","accent2":"#d49ab5"}
if __name__ == "__main__":
    run(CONFIG)
