#!/usr/bin/env python3
"""Sauvegarde de tous les stores (*_data.json) vers un Google Sheet.

Sécurité / durabilité : 2e copie indépendante de git, sauvegardée par Google
(versions + export). Lancé 1x/jour par le workflow sheets-backup.

Config (secrets GitHub injectés en variables d'env) :
  - GOOGLE_SA_KEY : le JSON de la clé du compte de service (collé tel quel)
  - SHEET_ID      : l'identifiant du Google Sheet (dans son URL)

Le compte de service doit avoir accès en édition au Sheet (le partager avec
l'email du compte de service). Si les secrets sont absents, le script ne fait
rien (n'échoue pas).

Un onglet par enseigne (nom = clé du fichier), + un onglet "_RECAP".
"""
import glob
import json
import os
import sys
import datetime as dt

# ordre de colonnes lisible ; les autres clés suivent en ordre alpha
PRIORITY = ["date", "jour", "heure", "fin", "lieu", "salle", "terrain", "cours",
            "coach", "capacite", "presents", "reserves", "noshow", "statut",
            "places_restantes", "prix", "duree", "finie", "locked", "id",
            "court_id", "releve", "premier_vu", "dernier_vu", "vu_dispo"]


def order_cols(keys):
    keys = set(keys)
    head = [k for k in PRIORITY if k in keys]
    return head + sorted(keys - set(head))


def rows_of(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    recs = list(data.values()) if isinstance(data, dict) else data
    return [r for r in recs if isinstance(r, dict)]


def main():
    key = os.environ.get("GOOGLE_SA_KEY", "").strip()
    sheet_id = os.environ.get("SHEET_ID", "").strip()
    if not key or not sheet_id:
        print("GOOGLE_SA_KEY / SHEET_ID absents -> sauvegarde Sheets ignorée.", file=sys.stderr)
        return
    import gspread
    from google.oauth2.service_account import Credentials
    creds = Credentials.from_service_account_info(
        json.loads(key), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    sh = gspread.authorize(creds).open_by_key(sheet_id)

    recap = [["Enseigne", "Séances enregistrées", "Dernière sauvegarde (UTC)"]]
    now = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    files = sorted(glob.glob("*_data.json"))
    for path in files:
        name = path.replace("_data.json", "")
        try:
            recs = rows_of(path)
        except Exception as e:  # noqa: BLE001
            print(f"  {path}: lecture échouée ({e})", file=sys.stderr)
            continue
        if not recs:
            recap.append([name, 0, now])
            continue
        cols = order_cols({k for r in recs for k in r})
        # tri stable par date+heure si présent
        recs.sort(key=lambda r: (str(r.get("date", "")), str(r.get("heure", ""))))
        values = [cols] + [[_cell(r.get(c)) for c in cols] for r in recs]
        title = name[:90]
        try:
            ws = sh.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=title, rows=len(values) + 10, cols=len(cols) + 2)
        ws.resize(rows=max(len(values), 1), cols=max(len(cols), 1))
        ws.clear()
        ws.update(values, value_input_option="RAW")
        recap.append([name, len(recs), now])
        print(f"  -> onglet {title}: {len(recs)} séances")

    # onglet récap
    try:
        rw = sh.worksheet("_RECAP")
    except gspread.WorksheetNotFound:
        rw = sh.add_worksheet(title="_RECAP", rows=len(recap) + 5, cols=3)
    rw.resize(rows=max(len(recap), 1), cols=3)
    rw.clear()
    rw.update(recap, value_input_option="RAW")
    print(f"OK : {len(files)} enseignes sauvegardées dans le Sheet {sheet_id}.")


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "oui" if v else "non"
    return v


if __name__ == "__main__":
    main()
