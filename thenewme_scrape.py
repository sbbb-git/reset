#!/usr/bin/env python3
"""The New Me (bsport, multi-companies) — voir bsport_scrape.py."""
from bsport_scrape import run
# 18 companies bsport couvrant TOUS les studios The New Me (Paris + régions)
CONFIG = {"key":"thenewme","brand":"THE NEW ME",
          "companies":[4272,4273,4275,4276,4277,4279,4283,4284,4287,
                       4902,4903,4904,4905,4907,4908,4909,4917,4986],
          "host":"thenewmeparis.com","store":"thenewme_data.json",
          "html":"thenewme.html","csv":"thenewme_seances.csv","price":20,
          "accent":"#b8895e","accent2":"#d8b08a"}
if __name__ == "__main__":
    run(CONFIG)
