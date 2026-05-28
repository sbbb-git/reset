// Récupère le planning Mindbody de Sense-Club pour la journée affichée (aujourd'hui).
// Sortie: JSON sur stdout -> [{heure, duree, cours, statut_brut}]
let chromium;
try { ({ chromium } = require('playwright')); }
catch (e) { ({ chromium } = require('/opt/node22/lib/node_modules/playwright')); }
const URL = "https://go.mindbodyonline.com/book/widgets/schedules/view/f310828c2e2/schedule";
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ ignoreHTTPSErrors:true, locale:'fr-FR' });
  const p = await ctx.newPage();
  await p.goto(URL, { waitUntil:'domcontentloaded', timeout:60000 }).catch(()=>{});
  await p.waitForFunction(
    () => /(?:Réserver|Liste d'attente|Complet|place)/i.test(document.body.innerText) && /\d{1,2}:\d{2}/.test(document.body.innerText),
    { timeout:30000 }).catch(()=>{});
  await p.waitForTimeout(2500);
  const raw = await p.evaluate(() => document.body.innerText);
  await b.close();
  const txt = raw.replace(/\s+/g, ' ');
  const re = /(\d{1,2}:\d{2})\s+(\d+)\s*min\s+(.+?)\s+Afficher les détails\s+Sense Club\s+(Réserver|Liste d'attente|Complet|Il ne reste qu'une place\s*!?|Il reste\s+\d+\s+places?\s*!?)/gi;
  const out = []; let m;
  while ((m = re.exec(txt))) {
    out.push({ heure:m[1], duree:+m[2], cours:m[3].replace(/\s+/g,' ').trim(), statut_brut:m[4].replace(/\s+/g,' ').trim() });
  }
  process.stdout.write(JSON.stringify(out));
})();
