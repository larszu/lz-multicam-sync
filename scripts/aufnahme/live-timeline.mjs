// Nimmt docs/screenshots/live-timeline-{light,dark}.gif aus dem Demo-Ablauf auf.
// Aufruf: cd scripts/aufnahme && npm install && npm run live-timeline
// Braucht Google Chrome (oder CHROME=<Pfad>) und ffmpeg im PATH.
import { chromium } from 'playwright-core';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const PAGE = pathToFileURL(join(ROOT, 'lzsync/ui/index.html')).href;
const OUT = join(ROOT, 'docs/screenshots');
const CHROME = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const FPS = 8, WIDTH = 820, TAIL_S = 2.5;

const browser = await chromium.launch({ executablePath: CHROME });
for (const theme of ['light', 'dark']) {
  const dir = mkdtempSync(join(tmpdir(), 'lzsync-gif-'));
  const page = await browser.newPage({ viewport: { width: 1180, height: 760 }, deviceScaleFactor: 2, colorScheme: theme });
  await page.goto(`${PAGE}?demo=sim&theme=${theme}`);
  const block = page.locator('#tl-block');
  await block.waitFor({ state: 'visible' });
  // Kopfleiste und Rand ausblenden: nur die Timeline-Karte, ohne Sticky-Schatten-Abschnitt
  await page.addStyleTag({ content: '.tl-block{position:static!important}' });
  let n = 0, doneAt = null;
  const t0 = Date.now();
  while (true) {
    await block.screenshot({ path: join(dir, `f${String(n++).padStart(4, '0')}.png`), animations: 'allow' });
    const done = await page.locator('#view-result').isVisible();
    if (done && doneAt === null) doneAt = n;
    if (doneAt !== null && n - doneAt >= TAIL_S * FPS) break;
    if (Date.now() - t0 > 60000) throw new Error('Demo-Ablauf endet nicht');
    const wait = t0 + (n * 1000) / FPS - Date.now();
    if (wait > 0) await page.waitForTimeout(wait);
  }
  await page.close();
  const gif = join(OUT, `live-timeline-${theme}.gif`);
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-framerate', String(FPS), '-i', join(dir, 'f%04d.png'),
    '-vf', `scale=${WIDTH}:-2:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle`,
    gif]);
  console.log(`${gif}: ${readdirSync(dir).length} Bilder`);
  rmSync(dir, { recursive: true, force: true });
}
await browser.close();
