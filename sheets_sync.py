#!/usr/bin/env python3
"""Sauvegarde de tous les stores (*_data.json) vers un Google Sheet — méthode
SIMPLE (sans Google Cloud ni clé JSON).

Le Google Sheet contient un petit script (Apps Script) déployé en "web app"
qui reçoit les données et les écrit. Ici on lui envoie simplement tout par un
POST HTTP. Un seul secret nécessaire :
  - SHEET_WEBHOOK_URL : l'URL de la web app Apps Script (se termine par /exec)

Si le secret est absent, le script ne fait rien (n'échoue pas).
"""
import glob
import json
import os
import sys
import urllib.request


def main():
    url = os.environ.get("SHEET_WEBHOOK_URL", "").strip()
    if not url:
        print("SHEET_WEBHOOK_URL absent -> sauvegarde Sheets ignorée.", file=sys.stderr)
        return
    payload = {}
    for path in sorted(glob.glob("*_data.json")):
        name = path.replace("_data.json", "")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:  # noqa: BLE001
            print(f"  {path}: lecture échouée ({e})", file=sys.stderr)
            continue
        recs = list(data.values()) if isinstance(data, dict) else data
        recs = [r for r in recs if isinstance(r, dict)]
        recs.sort(key=lambda r: (str(r.get("date", "")), str(r.get("heure", ""))))
        payload[name] = recs
    body = json.dumps({"data": payload}).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        print("Réponse Google Sheets :", r.read().decode("utf-8", "ignore")[:200])
    print(f"OK : {len(payload)} enseignes envoyées au Sheet.")


if __name__ == "__main__":
    main()
