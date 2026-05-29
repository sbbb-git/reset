#!/usr/bin/env python3
"""The New Me (bsport, multi-companies) — voir bsport_scrape.py.

Companies découvertes en crawlant thenewmeparis.com (toutes les pages studio).
Couvre Paris + toutes les régions + Bruxelles. Les villes "coming soon" sans
widget bsport (Amsterdam, Athens, Dubai, Madrid, Genève, Monaco, Nice, etc.)
s'ajouteront quand elles auront un companyId.
"""
from bsport_scrape import run
CONFIG = {"key":"thenewme","brand":"THE NEW ME",
          "companies":[4272,4273,4275,4276,4277,4278,4279,4283,4284,4285,4287,
                       4902,4903,4904,4905,4906,4907,4908,4909,4913,4915,4917,
                       4919,4920,4921,4986,5699],
          "host":"thenewmeparis.com","store":"thenewme_data.json",
          "html":"thenewme.html","csv":"thenewme_seances.csv","price":20,
          "accent":"#b8895e","accent2":"#d8b08a"}
if __name__ == "__main__":
    run(CONFIG)
