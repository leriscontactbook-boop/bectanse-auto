import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const sharp = require('sharp');

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectDir = path.resolve(scriptDir, '..');
const outputDir = path.join(projectDir, 'static', 'telegram-visuals');
const masterPath = path.join(outputDir, 'bectanse-market-signal-master.png');

const WIDTH = 1200;
const HEIGHT = 600;
const ACCESS_URL = 'https://acces.bectanse-academie.com/';

const templates = [
  {
    id: 'session-londres',
    filename: '01-session-londres-ouverte-v2',
    eyebrow: 'OUVERTURE DE MARCHÉ',
    title: ['SESSION LONDRES', 'OUVERTE'],
    metric: '09:00 PARIS',
    status: 'MARCHÉ EN DIRECT',
    accent: '#38BDF8',
    accentSoft: '#6F42C1',
    icon: 'candles',
    flag: 'uk',
    topBadge: 'LONDON • LSE',
    detail: 'timezone',
    titleSize: 70,
    metricWidth: 310,
    ctaText: 'VOIR LE PLAN LONDRES',
    caption: 'La session de Londres est ouverte. Les niveaux et scénarios du jour sont disponibles dans ton espace.'
  },
  {
    id: 'session-us',
    filename: '02-session-americaine-t30-v2',
    eyebrow: 'COMPTE À REBOURS',
    title: ['SESSION US', 'DANS 30 MINUTES'],
    metric: 'T − 30 MIN',
    status: 'PRÉPARATION ACTIVE',
    accent: '#F06A16',
    accentSoft: '#FF4D67',
    icon: 'countdown',
    flag: 'us',
    topBadge: 'NEW YORK • NYSE',
    detail: 'wallstreet',
    titleSize: 68,
    metricWidth: 310,
    ctaText: 'PRÉPARER LA SESSION US',
    caption: 'La session américaine approche. Vérifie ton plan, ton risque et les zones que tu souhaites observer.'
  },
  {
    id: 'annonce-economique',
    filename: '03-annonce-economique-majeure-v2',
    eyebrow: 'FLASH ÉCONOMIQUE',
    title: ['ANNONCE ÉCONOMIQUE', 'MAJEURE'],
    metric: '14:30 PARIS',
    status: 'IMPACT ÉLEVÉ',
    accent: '#FFB020',
    accentSoft: '#F06A16',
    icon: 'warning',
    topBadge: 'MACRO • HIGH IMPACT',
    detail: 'calendar',
    titleSize: 63,
    metricWidth: 310,
    ctaText: 'VOIR LE CALENDRIER',
    caption: 'Une annonce économique majeure est attendue. Consulte l’horaire et les informations disponibles avant la publication.'
  },
  {
    id: 'resultat-journee',
    filename: '04-resultat-de-la-journee-v2',
    eyebrow: 'BILAN BECTANSE',
    title: ['RÉSULTAT', 'DE LA JOURNÉE'],
    metric: 'BILAN DISPONIBLE',
    status: 'PERFORMANCE • DISCIPLINE',
    accent: '#32D583',
    accentSoft: '#38BDF8',
    icon: 'result',
    topBadge: 'BILAN • VÉRIFIÉ',
    detail: 'ledger',
    titleSize: 70,
    metricWidth: 350,
    ctaText: 'VOIR LES RÉSULTATS',
    caption: 'Le bilan de la journée est disponible. Retrouve les résultats et les éléments partagés avec la communauté.'
  },
  {
    id: 'quiz-marche',
    filename: '05-quiz-du-marche-v2',
    eyebrow: 'FORMAT INTERACTIF',
    title: ['QUIZ', 'DU MARCHÉ'],
    metric: 'QUESTION DU JOUR',
    status: 'TESTE TON ANALYSE',
    accent: '#9B6DFF',
    accentSoft: '#38BDF8',
    icon: 'quiz',
    topBadge: 'A • B • C',
    detail: 'answers',
    titleSize: 72,
    metricWidth: 340,
    ctaText: '',
    caption: 'Observe le marché, choisis ta réponse et vérifie ton raisonnement avec le quiz du jour.'
  },
  {
    id: 'nouveau-temoignage',
    filename: '06-nouveau-temoignage-v2',
    eyebrow: 'COMMUNAUTÉ BECTANSE',
    title: ['NOUVEAU', 'TÉMOIGNAGE'],
    metric: 'PREUVE COMMUNAUTÉ',
    status: 'AUDIO • TEXTE • VIDÉO',
    accent: '#E8B866',
    accentSoft: '#F06A16',
    icon: 'testimonial',
    topBadge: 'COMMUNAUTÉ • VÉRIFIÉE',
    detail: 'voice',
    titleSize: 70,
    metricWidth: 405,
    ctaText: 'VOIR LES TÉMOIGNAGES',
    caption: 'Un nouveau témoignage vient d’être partagé par la communauté Bectanse.'
  },
  {
    id: 'derniere-alerte',
    filename: '07-derniere-alerte-disponible-v2',
    eyebrow: 'BECTANSE SIGNAL',
    title: ['DERNIÈRE ALERTE', 'DISPONIBLE'],
    metric: 'ALERTE ACTIVE',
    status: 'ESPACE BECTANSE',
    accent: '#FF4D67',
    accentSoft: '#F06A16',
    icon: 'alert',
    topBadge: 'SIGNAL • LIVE',
    detail: 'radar',
    titleSize: 68,
    metricWidth: 325,
    ctaText: 'OUVRIR L’ESPACE MEMBRE',
    caption: 'Une nouvelle alerte est disponible dans ton espace Bectanse.'
  }
];

function escapeXml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
}

function flagMarkup(flag) {
  if (flag === 'uk') {
    return `
      <g clip-path="url(#flagClip)">
        <rect width="64" height="36" fill="#012169"/>
        <path d="M0 0L64 36M64 0L0 36" stroke="#FFFFFF" stroke-width="10"/>
        <path d="M0 0L64 36M64 0L0 36" stroke="#C8102E" stroke-width="4"/>
        <path d="M32 0V36M0 18H64" stroke="#FFFFFF" stroke-width="12"/>
        <path d="M32 0V36M0 18H64" stroke="#C8102E" stroke-width="7"/>
      </g>`;
  }
  if (flag === 'us') {
    const stripes = Array.from({ length: 13 }, (_, index) => (
      `<rect y="${(index * 36 / 13).toFixed(3)}" width="64" height="${(36 / 13 + 0.15).toFixed(3)}" fill="${index % 2 === 0 ? '#B22234' : '#FFFFFF'}"/>`
    )).join('');
    const stars = Array.from({ length: 15 }, (_, index) => {
      const column = index % 5;
      const row = Math.floor(index / 5);
      return `<circle cx="${4.2 + column * 5.2}" cy="${4.2 + row * 5.3}" r="1.05" fill="#FFFFFF"/>`;
    }).join('');
    return `<g clip-path="url(#flagClip)">${stripes}<rect width="29" height="18.5" fill="#3C3B6E"/>${stars}</g>`;
  }
  return '';
}

function topBadgeMarkup(template) {
  const label = escapeXml(template.topBadge || 'BECTANSE MARKET');
  const fontSize = label.length > 20 ? 10.5 : label.length > 15 ? 11.5 : 13;
  if (template.flag) {
    return `
      <g transform="translate(906 46)">
        <rect width="232" height="56" rx="15" fill="#09090D" fill-opacity=".78" stroke="#FFFFFF" stroke-opacity=".16"/>
        <g transform="translate(10 10)">
          ${flagMarkup(template.flag)}
          <rect width="64" height="36" rx="6" fill="none" stroke="#FFFFFF" stroke-opacity=".24"/>
        </g>
        <text x="86" y="34" fill="#FFFFFF" font-family="Avenir Next Condensed, Arial Narrow" font-size="${fontSize}" font-weight="700" letter-spacing="1.7">${label}</text>
      </g>`;
  }
  return `
    <g transform="translate(914 46)">
      <rect width="224" height="52" rx="14" fill="#09090D" fill-opacity=".76" stroke="${template.accent}" stroke-opacity=".36"/>
      <circle cx="24" cy="26" r="6" fill="${template.accent}"/>
      <circle cx="24" cy="26" r="12" fill="${template.accent}" opacity=".13"/>
      <text x="44" y="32" fill="#FFFFFF" font-family="Avenir Next Condensed, Arial Narrow" font-size="${fontSize}" font-weight="700" letter-spacing="1.8">${label}</text>
    </g>`;
}

function detailMarkup(template) {
  const accent = template.accent;
  const stroke = `stroke="${accent}" fill="none" stroke-linecap="round" stroke-linejoin="round"`;
  if (template.detail === 'timezone') {
    return `<g opacity=".52" ${stroke}>
      <path d="M857 447c45-104 159-147 253-95" stroke-width="2" stroke-dasharray="5 10"/>
      <circle cx="875" cy="421" r="4" fill="${accent}"/><circle cx="1110" cy="352" r="4" fill="${accent}"/>
      <path d="M1090 465h42M1111 444v42" stroke-width="2"/>
    </g>`;
  }
  if (template.detail === 'wallstreet') {
    return `<g opacity=".48" ${stroke} stroke-width="2">
      <path d="M856 489h278M876 472v17M910 456v33M944 465v24M1074 448v41M1108 432v57"/>
      <path d="M862 365l26-20 27 9 28-31 29 13" stroke-dasharray="5 8"/>
    </g>`;
  }
  if (template.detail === 'calendar') {
    return `<g opacity=".58">
      <rect x="850" y="336" width="70" height="54" rx="9" fill="#09090D" stroke="${accent}" stroke-opacity=".55"/>
      <rect x="928" y="313" width="70" height="54" rx="9" fill="#09090D" stroke="${accent}" stroke-opacity=".55"/>
      <rect x="1006" y="336" width="70" height="54" rx="9" fill="#09090D" stroke="${accent}" stroke-opacity=".55"/>
      <path d="M850 352h70M928 329h70M1006 352h70" stroke="${accent}" stroke-width="3"/>
      <circle cx="873" cy="370" r="4" fill="${accent}"/><circle cx="951" cy="347" r="4" fill="${accent}"/><circle cx="1029" cy="370" r="4" fill="${accent}"/>
    </g>`;
  }
  if (template.detail === 'ledger') {
    return `<g opacity=".42" ${stroke} stroke-width="2">
      <path d="M850 340h94M850 362h74M850 384h104M1050 340h72M1050 362h84M1050 384h62"/>
      <circle cx="1098" cy="463" r="21"/><path d="M1088 463l7 8 17-21" stroke-width="5"/>
    </g>`;
  }
  if (template.detail === 'answers') {
    return `<g font-family="DIN Condensed, Arial Narrow" font-size="19" font-weight="700" text-anchor="middle">
      <g transform="translate(887 364)"><circle r="22" fill="#09090D" stroke="${accent}" stroke-opacity=".7"/><text y="7" fill="#FFFFFF">A</text></g>
      <g transform="translate(1092 376)"><circle r="22" fill="#09090D" stroke="${accent}" stroke-opacity=".7"/><text y="7" fill="#FFFFFF">B</text></g>
      <g transform="translate(1084 477)"><circle r="22" fill="#09090D" stroke="${accent}" stroke-opacity=".7"/><text y="7" fill="#FFFFFF">C</text></g>
    </g>`;
  }
  if (template.detail === 'voice') {
    return `<g opacity=".66" ${stroke} stroke-width="4">
      <path d="M850 473v-18M866 473v-36M882 473v-54M898 473v-27M1088 473v-29M1104 473v-48M1120 473v-22"/>
      <path d="M861 342c0-18 11-29 31-31v16c-9 2-13 7-13 15h14v30h-32zm45 0c0-18 11-29 31-31v16c-9 2-13 7-13 15h14v30h-32z" fill="${accent}" stroke="none" opacity=".7"/>
    </g>`;
  }
  return `<g opacity=".52" ${stroke} stroke-width="2">
    <circle cx="995" cy="413" r="102" stroke-dasharray="4 11"/>
    <circle cx="995" cy="413" r="132" stroke-dasharray="2 15"/>
    <path d="M843 413h36M1111 413h36M995 261v36M995 529v36" stroke-width="4"/>
  </g>`;
}

function iconMarkup(type, accent) {
  const stroke = `stroke="${accent}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round" fill="none"`;
  if (type === 'candles') {
    return `<g ${stroke}><path d="M930 408v92M908 435h44v38h-44zM995 368v116M973 397h44v54h-44zM1060 330v128M1038 359h44v62h-44z"/></g>`;
  }
  if (type === 'countdown') {
    return `<g ${stroke}><circle cx="995" cy="413" r="76" opacity=".9"/><path d="M995 337a76 76 0 0 1 66 38"/><path d="M995 413l32-42"/></g><text x="995" y="438" text-anchor="middle" fill="white" font-family="DIN Condensed, Arial Narrow" font-size="38" font-weight="700">30</text>`;
  }
  if (type === 'warning') {
    return `<g ${stroke}><path d="M995 326l92 160H903z"/><path d="M995 374v55M995 458h.1"/></g>`;
  }
  if (type === 'result') {
    return `<g ${stroke}><path d="M910 482h174"/><path d="M930 452v-48M980 452v-83M1030 452V342"/><path d="M918 375l48-38 43 16 68-62"/></g>`;
  }
  if (type === 'quiz') {
    return `<text x="995" y="478" text-anchor="middle" fill="${accent}" font-family="DIN Condensed, Arial Narrow" font-size="205" font-weight="700">?</text>`;
  }
  if (type === 'testimonial') {
    return `<g ${stroke}><path d="M913 448h30l20-72 31 111 29-93 22 54h37"/><path d="M920 331h150a24 24 0 0 1 24 24v116a24 24 0 0 1-24 24H958l-38 32v-32a24 24 0 0 1-24-24V355a24 24 0 0 1 24-24z" opacity=".75"/></g>`;
  }
  return `<g ${stroke}><circle cx="995" cy="413" r="74"/><circle cx="995" cy="413" r="35"/><path d="M995 305v-34M995 555v-34M887 413h-34M1137 413h-34"/><path d="M995 413l50-62"/></g>`;
}

function templateSvg(template) {
  const [line1, line2] = template.title.map(escapeXml);
  const eyebrow = escapeXml(template.eyebrow);
  const metric = escapeXml(template.metric);
  const status = escapeXml(template.status);
  const titleSize = template.titleSize || 68;
  const metricWidth = template.metricWidth || 320;
  return Buffer.from(`
    <svg width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="leftShade" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stop-color="#08080C" stop-opacity=".99"/>
          <stop offset=".46" stop-color="#08080C" stop-opacity=".93"/>
          <stop offset=".72" stop-color="#08080C" stop-opacity=".22"/>
          <stop offset="1" stop-color="#08080C" stop-opacity="0"/>
        </linearGradient>
        <linearGradient id="accentWash" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stop-color="${template.accent}" stop-opacity=".28"/>
          <stop offset=".52" stop-color="${template.accentSoft}" stop-opacity=".08"/>
          <stop offset="1" stop-color="${template.accent}" stop-opacity="0"/>
        </linearGradient>
        <filter id="glow"><feGaussianBlur stdDeviation="12"/></filter>
        <clipPath id="flagClip"><rect width="64" height="36" rx="6"/></clipPath>
      </defs>

      <rect width="1200" height="600" fill="url(#leftShade)"/>
      <rect width="1200" height="600" fill="url(#accentWash)"/>
      <circle cx="1000" cy="414" r="150" fill="${template.accent}" opacity=".08" filter="url(#glow)"/>
      <circle cx="1000" cy="414" r="112" fill="#09090D" fill-opacity=".48" stroke="${template.accent}" stroke-opacity=".26"/>
      ${detailMarkup(template)}

      <g transform="translate(62 43)">
        <rect width="58" height="58" rx="16" fill="#F06A16" fill-opacity=".14" stroke="#F06A16" stroke-opacity=".78"/>
        <text x="29" y="39" text-anchor="middle" fill="#F06A16" font-family="DIN Condensed, Arial Narrow" font-size="25" font-weight="700">B€</text>
        <text x="77" y="26" fill="#FFFFFF" font-family="DIN Condensed, Arial Narrow" font-size="27" font-weight="700" letter-spacing="2.4">BECTANSE</text>
        <text x="77" y="49" fill="#A8A8B3" font-family="Avenir Next Condensed, Arial Narrow" font-size="11" font-weight="700" letter-spacing="3.2">ACADÉMIE</text>
      </g>
      ${topBadgeMarkup(template)}

      <g transform="translate(62 132)">
        <rect width="7" height="26" rx="3.5" fill="${template.accent}"/>
        <text x="22" y="19" fill="${template.accent}" font-family="Avenir Next Condensed, Arial Narrow" font-size="14" font-weight="700" letter-spacing="2.8">${eyebrow}</text>
      </g>

      <text x="62" y="239" fill="#FFFFFF" font-family="DIN Condensed, Arial Narrow" font-size="${titleSize}" font-weight="700" letter-spacing=".7">${line1}</text>
      <text x="62" y="309" fill="#FFFFFF" font-family="DIN Condensed, Arial Narrow" font-size="${titleSize}" font-weight="700" letter-spacing=".7">${line2}</text>
      <rect x="62" y="328" width="92" height="5" rx="2.5" fill="${template.accent}"/>
      <rect x="160" y="328" width="34" height="5" rx="2.5" fill="${template.accent}" opacity=".32"/>

      <g transform="translate(62 360)">
        <rect width="${metricWidth}" height="64" rx="15" fill="#0B0B11" fill-opacity=".86" stroke="${template.accent}" stroke-opacity=".72"/>
        <circle cx="31" cy="32" r="7" fill="${template.accent}"/>
        <circle cx="31" cy="32" r="14" fill="${template.accent}" opacity=".16"/>
        <text x="56" y="42" fill="#FFFFFF" font-family="DIN Condensed, Arial Narrow" font-size="28" font-weight="700" letter-spacing="1.35">${metric}</text>
      </g>
      <text x="62" y="463" fill="#B6B6C0" font-family="Avenir Next Condensed, Arial Narrow" font-size="15" font-weight="700" letter-spacing="3">${status}</text>

      ${iconMarkup(template.icon, template.accent)}

      <line x1="62" y1="535" x2="1138" y2="535" stroke="#FFFFFF" stroke-opacity=".13"/>
      <text x="62" y="564" fill="#8B8B97" font-family="Avenir Next Condensed, Arial Narrow" font-size="10.5" font-weight="700" letter-spacing="2.45">BECTANSE ACADÉMIE  •  TRADING  •  HEURE DE PARIS</text>
      <text x="1138" y="564" text-anchor="end" fill="${template.accent}" font-family="DIN Condensed, Arial Narrow" font-size="12" font-weight="700" letter-spacing="1.65">LIVE MARKET INTELLIGENCE</text>
    </svg>
  `);
}

async function renderTemplate(template) {
  const pngPath = path.join(outputDir, `${template.filename}.png`);
  const webpPath = path.join(outputDir, `${template.filename}.webp`);
  const base = sharp(masterPath).resize(WIDTH, HEIGHT, { fit: 'cover', position: 'centre' });
  const pngBuffer = await base
    .composite([{ input: templateSvg(template), top: 0, left: 0 }])
    .png({ compressionLevel: 9, palette: false })
    .toBuffer();

  await fs.writeFile(pngPath, pngBuffer);
  await sharp(pngBuffer).webp({ quality: 92, effort: 6 }).toFile(webpPath);
  return { ...template, pngPath, webpPath };
}

async function renderContactSheet(rendered) {
  const thumbWidth = 720;
  const thumbHeight = 360;
  const gap = 34;
  const outer = 52;
  const titleHeight = 120;
  const columns = 2;
  const rows = Math.ceil(rendered.length / columns);
  const sheetWidth = outer * 2 + columns * thumbWidth + gap;
  const sheetHeight = titleHeight + outer + rows * thumbHeight + (rows - 1) * gap + outer;
  const composites = [];

  for (let index = 0; index < rendered.length; index += 1) {
    const left = outer + (index % columns) * (thumbWidth + gap);
    const top = titleHeight + outer + Math.floor(index / columns) * (thumbHeight + gap);
    const buffer = await sharp(rendered[index].pngPath)
      .resize(thumbWidth, thumbHeight)
      .png()
      .toBuffer();
    composites.push({ input: buffer, left, top });
  }

  const header = Buffer.from(`
    <svg width="${sheetWidth}" height="${sheetHeight}" xmlns="http://www.w3.org/2000/svg">
      <rect width="100%" height="100%" fill="#08080C"/>
      <text x="${outer}" y="66" fill="#FFFFFF" font-family="DIN Condensed, Arial Narrow" font-size="40" font-weight="700" letter-spacing="1.5">BECTANSE MARKET SIGNAL — V2</text>
      <text x="${outer}" y="97" fill="#8F8F9B" font-family="Avenir Next Condensed, Arial Narrow" font-size="15" font-weight="700" letter-spacing="3">DIRECTION ARTISTIQUE FINALE • 7 MODÈLES TELEGRAM • 1200 × 600</text>
    </svg>
  `);

  const contactSheetPath = path.join(outputDir, 'bectanse-market-signal-contact-sheet-v2.png');
  await sharp({ create: { width: sheetWidth, height: sheetHeight, channels: 4, background: '#08080C' } })
    .composite([{ input: header, left: 0, top: 0 }, ...composites])
    .png({ compressionLevel: 9 })
    .toFile(contactSheetPath);
  return contactSheetPath;
}

await fs.mkdir(outputDir, { recursive: true });
const rendered = [];
for (const template of templates) rendered.push(await renderTemplate(template));
const contactSheetPath = await renderContactSheet(rendered);

const manifest = {
  version: 2,
  system: 'Bectanse Market Signal V2',
  timezone: 'Europe/Paris',
  dimensions: { width: WIDTH, height: HEIGHT },
  masterBackground: path.relative(projectDir, masterPath),
  contactSheet: path.relative(projectDir, contactSheetPath),
  templates: rendered.map((item) => ({
    id: item.id,
    title: item.title.join(' '),
    png: path.relative(projectDir, item.pngPath),
    webp: path.relative(projectDir, item.webpPath),
    accent: item.accent,
    caption: item.caption,
    ctaText: item.ctaText,
    ctaUrl: item.ctaText ? ACCESS_URL : ''
  }))
};

await fs.writeFile(
  path.join(outputDir, 'bectanse-market-signal-manifest-v2.json'),
  `${JSON.stringify(manifest, null, 2)}\n`,
  'utf8'
);

console.log(JSON.stringify({ outputDir, contactSheetPath, files: rendered.map((item) => item.pngPath) }, null, 2));
