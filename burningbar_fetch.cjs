// Récupère le planning Mindbody de Burning Bar (2 salles) via le widget rendu.
// Sortie: JSON sur stdout -> [{salle, heure, duree, cours, statut_brut}]
let chromium;
try { ({ chromium } = require('playwright')); }
catch (e) { ({ chromium } = require('/opt/node22/lib/node_modules/playwright')); }

const ROOMS = [
  { salle: "The Hot Room",     id: "4d37769e50a" },
  { salle: "The Reformer Room", id: "4d43192e50a" },
];
const RE = /(\d{1,2}:\d{2})\s+(\d+)\s*min\s+(.+?)\s+(Réserver|Liste d'attente|Complet|Il ne reste qu'une place\s*!?|Il reste\s+\d+\s+places?\s*!?)/gi;

(async () => {
  const b = await chromium.launch();
  const out = [];
  for (const room of ROOMS) {
    const url = `https://go.mindbodyonline.com/book/widgets/schedules/view/${room.id}/schedule`;
    const ctx = await b.newContext({ ignoreHTTPSErrors: true, locale: 'fr-FR' });
    const p = await ctx.newPage();
    try {
      await p.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
      await p.waitForFunction(
        () => /(?:Réserver|Liste d'attente|Complet|place)/i.test(document.body.innerText) && /\d{1,2}:\d{2}/.test(document.body.innerText),
        { timeout: 30000 }).catch(() => {});
      await p.waitForTimeout(2500);
      const raw = await p.evaluate(() => document.body.innerText);
      const txt = raw.replace(/\s+/g, ' ');
      let m;
      while ((m = RE.exec(txt))) {
        let cours = m[3].split(/Afficher les détails/i)[0].replace(/\s+/g, ' ').trim();
        out.push({ salle: room.salle, heure: m[1], duree: +m[2], cours,
                   statut_brut: m[4].replace(/\s+/g, ' ').trim() });
      }
    } catch (e) { /* salle ignorée si échec */ }
    await ctx.close();
  }
  await b.close();
  process.stdout.write(JSON.stringify(out));
})();
