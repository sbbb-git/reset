/**
 * À COLLER dans le Google Sheet : Extensions → Apps Script → tout remplacer
 * par ce code → Enregistrer → Déployer → Nouveau déploiement → "Application web"
 * (Exécuter en tant que : Moi ; Accès : Tout le monde) → copier l'URL /exec.
 * Cette URL est le secret SHEET_WEBHOOK_URL à mettre dans GitHub.
 */
var PRIO = ["date","jour","heure","fin","lieu","salle","terrain","cours","coach",
  "capacite","presents","reserves","noshow","statut","places_restantes","prix",
  "duree","finie","locked","id","court_id","releve","premier_vu","dernier_vu"];

function doPost(e) {
  var payload = JSON.parse(e.postData.contents).data || {};
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var now = new Date().toISOString().slice(0, 16).replace("T", " ");
  var recap = [["Enseigne", "Séances", "Dernière sauvegarde (UTC)"]];
  Object.keys(payload).forEach(function (name) {
    var recs = payload[name] || [];
    var keys = {};
    recs.forEach(function (r) { Object.keys(r).forEach(function (k) { keys[k] = 1; }); });
    var cols = PRIO.filter(function (k) { return keys[k]; })
      .concat(Object.keys(keys).filter(function (k) { return PRIO.indexOf(k) < 0; }).sort());
    var sh = ss.getSheetByName(name) || ss.insertSheet(name);
    sh.clearContents();
    if (!recs.length) { recap.push([name, 0, now]); return; }
    var rows = [cols];
    recs.forEach(function (r) {
      rows.push(cols.map(function (c) {
        var v = r[c];
        return (v === undefined || v === null) ? "" : (v === true ? "oui" : v === false ? "non" : v);
      }));
    });
    sh.getRange(1, 1, rows.length, cols.length).setValues(rows);
    recap.push([name, recs.length, now]);
  });
  var rs = ss.getSheetByName("_RECAP") || ss.insertSheet("_RECAP");
  rs.clearContents();
  rs.getRange(1, 1, recap.length, 3).setValues(recap);
  return ContentService.createTextOutput("OK " + Object.keys(payload).length + " enseignes");
}
