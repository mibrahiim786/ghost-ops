'use strict';

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 1 — CONSTANTS & CONFIG
// ─────────────────────────────────────────────────────────────────────────────

const os   = require('os');
const fs   = require('fs');
const path = require('path');

const DIMENSIONS = [
  { key: 'roleClarity',         label: 'Role Clarity',             icon: '💥' },
  { key: 'constraintDensity',   label: 'Constraint Density',       icon: '🛡️'  },
  { key: 'hallucinationGuards', label: 'Hallucination Guardrails', icon: '📡' },
  { key: 'outputSpecificity',   label: 'Output Specificity',       icon: '🗺️'  },
  { key: 'testability',         label: 'Testability',              icon: '🎯' },
  { key: 'escapeHatches',       label: 'Escape Hatches',           icon: '⚡' },
];

const COLOR_THRESHOLDS = { red: 39, yellow: 69 };

const SELF_TEST_DIR = path.join(os.homedir(), '.copilot', 'agents');

const PROFILES = {
  balanced:  { roleClarity: 1.0, constraintDensity: 1.0, hallucinationGuards: 1.0, outputSpecificity: 1.0, testability: 1.0, escapeHatches: 1.0 },
  security:  { roleClarity: 0.8, constraintDensity: 1.5, hallucinationGuards: 2.0, outputSpecificity: 0.8, testability: 1.0, escapeHatches: 1.5 },
  creative:  { roleClarity: 1.5, constraintDensity: 0.5, hallucinationGuards: 0.8, outputSpecificity: 1.5, testability: 0.5, escapeHatches: 1.0 },
  'ci-gate': { roleClarity: 1.0, constraintDensity: 1.2, hallucinationGuards: 1.5, outputSpecificity: 1.0, testability: 2.0, escapeHatches: 1.2 },
  assistant: { roleClarity: 1.5, constraintDensity: 1.0, hallucinationGuards: 1.5, outputSpecificity: 1.2, testability: 0.8, escapeHatches: 1.0 },
};

const HEURISTICS = {
  roleClarity: [
    { pattern: /^#\s+.{10,}/m,                              weight:  15, maxApply: 1 },
    { pattern: /\byou are\b/i,                              weight:  20, maxApply: 1 },
    { pattern: /\byour (role|job|task|mission|purpose)\b/i, weight:  20, maxApply: 1 },
    { pattern: /\bact as\b/i,                               weight:  15, maxApply: 1 },
    { pattern: /\bspecialist|expert|agent|assistant\b/i,    weight:  10, maxApply: 2 },
    { pattern: /\bdo not\b.{0,30}\bact as\b/i,              weight: -10, maxApply: 1 },
    { pattern: /\bresponsibilities\b/i,                     weight:  10, maxApply: 1 },
    { fn: (text) => text.split('\n').length < 50 ? -10 : 0 },
  ],
  constraintDensity: [
    { pattern: /\bnever\b/i,                                weight:  8, maxApply: 3 },
    { pattern: /\balways\b/i,                               weight:  6, maxApply: 3 },
    { pattern: /\bmust( not)?\b/i,                          weight:  6, maxApply: 4 },
    { pattern: /\bdo not\b/i,                               weight:  6, maxApply: 4 },
    { pattern: /\bonly\b.{0,40}(when|if|unless)/i,          weight:  8, maxApply: 3 },
    { pattern: /\bforbidden|prohibited|disallowed\b/i,      weight: 10, maxApply: 2 },
    { pattern: /\bexcept\b/i,                               weight:  5, maxApply: 2 },
    { pattern: /\blimit(ed)? to\b/i,                        weight:  8, maxApply: 2 },
    { pattern: /\bunder no circumstances\b/i,               weight: 12, maxApply: 1 },
    {
      fn: (text) => {
        const lines = text.split('\n');
        let inSection = false;
        let count = 0;
        for (const line of lines) {
          if (/^#+\s+(Rules|Constraints|Restrictions)/i.test(line)) {
            inSection = true;
            continue;
          }
          if (inSection && /^#+/.test(line.trim())) {
            inSection = false;
          }
          if (inSection && /^\s*-\s+/.test(line)) {
            count++;
          }
        }
        return Math.min(count, 5) * 4;
      },
    },
  ],
  hallucinationGuards: [
    { pattern: /\bdo not (make up|fabricate|invent|hallucinate)\b/i,        weight: 20, maxApply: 1 },
    { pattern: /\bif (you are|you're) (not sure|uncertain|unsure)\b/i,      weight: 15, maxApply: 1 },
    { pattern: /\bonly (use|rely on|cite) (verified|real|actual|provided)\b/i, weight: 15, maxApply: 1 },
    { pattern: /\bdo not (guess|assume|speculate)\b/i,                      weight: 12, maxApply: 2 },
    { pattern: /\bsay (so|that you don't know)\b/i,                         weight: 10, maxApply: 1 },
    { pattern: /\bcite\b.{0,30}\bsource/i,                                  weight: 10, maxApply: 1 },
    { pattern: /\bground(ed)?\b.{0,30}(in|on)\b/i,                          weight:  8, maxApply: 1 },
    { pattern: /\bverif(y|ied|iable)\b/i,                                    weight:  8, maxApply: 2 },
    { pattern: /\bwhen in doubt\b/i,                                         weight:  8, maxApply: 1 },
  ],
  outputSpecificity: [
    { pattern: /\bformat(ted)?\b/i,                                          weight: 10, maxApply: 1 },
    { pattern: /\b(json|yaml|markdown|csv|xml|table)\b/i,                    weight: 15, maxApply: 2 },
    { pattern: /\bstructure(d)?\b/i,                                         weight:  8, maxApply: 1 },
    { pattern: /\b(max|maximum|limit)\s+\d+\s+(words|lines|chars|tokens)\b/i, weight: 15, maxApply: 1 },
    { pattern: /\bsection(s)?\b/i,                                           weight:  8, maxApply: 1 },
    { pattern: /\bbullet(s|ed)?|numbered list\b/i,                           weight:  8, maxApply: 1 },
    { pattern: /\bheading(s)?\b/i,                                           weight:  8, maxApply: 1 },
    { pattern: /\bexample output\b/i,                                        weight: 20, maxApply: 1 },
    { pattern: /\bdo not include\b.{0,40}(explanation|preamble|intro)\b/i,   weight: 10, maxApply: 1 },
    { pattern: /\bstart (your response|with|by)\b/i,                         weight:  8, maxApply: 1 },
    { pattern: /\bend (your response|with)\b/i,                              weight:  8, maxApply: 1 },
  ],
  testability: [
    { pattern: /\bexpected (output|result|behavior)\b/i,                     weight: 20, maxApply: 1 },
    { pattern: /\bfor example\b/i,                                           weight: 12, maxApply: 3 },
    { pattern: /\bgiven.{0,40}(input|prompt|request)\b/i,                    weight: 12, maxApply: 2 },
    { pattern: /\b(input|output):\s/i,                                       weight: 15, maxApply: 2 },
    { pattern: /\bshould (return|output|produce|respond)\b/i,                weight: 12, maxApply: 3 },
    { pattern: /\bmust (return|output|produce|always return)\b/i,            weight: 12, maxApply: 2 },
    { pattern: /\btest\b/i,                                                  weight:  8, maxApply: 2 },
    { pattern: /\bdeterministic\b/i,                                         weight: 15, maxApply: 1 },
    { pattern: /\bif.{0,60}then\b/i,                                         weight:  8, maxApply: 3 },
    { pattern: /\bappropriately|as needed|use judgment\b/i,                  weight: -8, maxApply: 3 },
  ],
  escapeHatches: [
    { pattern: /\bif (you can't|you cannot|unable to)\b/i,                   weight: 15, maxApply: 2 },
    { pattern: /\bfall(s)? (back|through)\b/i,                               weight: 15, maxApply: 1 },
    { pattern: /\boutside (your|the) (scope|capability|expertise)\b/i,       weight: 15, maxApply: 1 },
    { pattern: /\bescalat(e|ion)\b/i,                                        weight: 12, maxApply: 1 },
    { pattern: /\brefuse\b.{0,40}(if|when)\b/i,                              weight: 12, maxApply: 2 },
    { pattern: /\bdefault (to|behavior)\b/i,                                 weight: 10, maxApply: 1 },
    { pattern: /\bwhen (not sure|unsure|uncertain|ambiguous)\b/i,             weight: 12, maxApply: 2 },
    { pattern: /\bdo not (attempt|try)\b.{0,30}(if|when|unless)\b/i,         weight: 10, maxApply: 2 },
    { pattern: /\bstate that\b.{0,40}(cannot|don't|won't)\b/i,               weight: 10, maxApply: 1 },
    { pattern: /\berror(s)?\b.{0,40}(handle|report|surface)\b/i,             weight: 10, maxApply: 1 },
  ],
};

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 2 — SCORING ENGINE  (pure, no I/O)
// ─────────────────────────────────────────────────────────────────────────────

// Split text into sentence-like segments for density analysis
function getSentences(text) {
  return text.split(/(?<=[.!?])\s+|\n/).map(s => s.trim()).filter(Boolean);
}

// Count words in a string
function wordCount(str) {
  return str.trim().split(/\s+/).filter(Boolean).length;
}

// Density multiplier: penalizes keywords in thin sentences (anti-gaming)
function densityMultiplier(text, matchIndex) {
  const sentences = getSentences(text);
  let pos = 0;
  for (const sentence of sentences) {
    const end = pos + sentence.length;
    if (matchIndex >= pos && matchIndex < end) {
      const wc = wordCount(sentence);
      if (wc < 5) return 0.0;
      if (wc < 10) return 0.3;
      return 1.0;
    }
    pos = text.indexOf(sentence, pos) + sentence.length;
  }
  return 1.0;
}

function applyHeuristics(text, rules) {
  let score = 0;
  const excerpts = [];
  for (const rule of rules) {
    if (typeof rule.fn === 'function') {
      score += rule.fn(text);
      continue;
    }
    const pat = rule.pattern;
    const flags = pat.flags.includes('g') ? pat.flags : pat.flags + 'g';
    const globalPat = new RegExp(pat.source, flags);
    let match;
    let applied = 0;
    while ((match = globalPat.exec(text)) !== null && applied < rule.maxApply) {
      const dm = densityMultiplier(text, match.index);
      score += rule.weight * dm;
      applied++;
      // Collect the surrounding sentence as an excerpt
      const start = text.lastIndexOf('\n', match.index) + 1;
      const end = text.indexOf('\n', match.index);
      excerpts.push(text.slice(start, end === -1 ? undefined : end).trim());
    }
  }
  return { score: Math.max(0, Math.min(100, Math.round(score))), excerpts };
}

function scoreDimension(text, dim) {
  return applyHeuristics(text, HEURISTICS[dim]);
}

function scoreText(text, filePath, opts) {
  if (!opts) opts = {};
  const dimensions = {};
  const allExcerpts = {};
  for (const { key } of DIMENSIONS) {
    const result = scoreDimension(text, key);
    dimensions[key] = result.score;
    allExcerpts[key] = result.excerpts;
  }
  const comp = composite(dimensions, opts.profile);
  const lineCount = text.split('\n').length;
  const wordCount = text.trim().split(/\s+/).filter(Boolean).length;
  return {
    file: path.resolve(filePath),
    dimensions,
    excerpts: allExcerpts,
    composite: comp,
    lineCount,
    wordCount,
    profile: opts.profile || 'balanced',
  };
}

function composite(dims, profileName) {
  const keys = DIMENSIONS.map(d => d.key);
  const weights = PROFILES[profileName] || PROFILES.balanced;
  let weightedSum = 0;
  let totalWeight = 0;
  for (const k of keys) {
    const w = weights[k] || 1.0;
    weightedSum += (dims[k] || 0) * w;
    totalWeight += w * 100; // max possible per dimension is 100
  }
  return Math.round((weightedSum / totalWeight) * 100);
}

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 2b — STRICT MODE (LLM evaluation, optional)
// ─────────────────────────────────────────────────────────────────────────────

const STRICT_MODEL    = process.env.OPENAI_MODEL    || 'gpt-4o-mini';
const STRICT_BASE_URL = process.env.OPENAI_BASE_URL || 'https://api.openai.com/v1';

async function strictEvaluate(dimensionLabel, excerpts) {
  const uniqueExcerpts = [...new Set(excerpts)].slice(0, 20);
  const body = {
    model: STRICT_MODEL,
    temperature: 0.1,
    messages: [
      {
        role: 'system',
        content: 'You evaluate AI agent prompt instructions for quality.\n' +
          'Given matched keyword excerpts from a "' + dimensionLabel + '" scan, rate whether these represent:\n' +
          '- Coherent, actionable instructions (1.0)\n' +
          '- Partially meaningful but vague (0.5)\n' +
          '- Keyword stuffing / meaningless filler (0.0)\n' +
          'Respond ONLY with JSON: {"multiplier": <0.0-1.0>, "reasoning": "<one sentence>"}'
      },
      {
        role: 'user',
        content: 'Dimension: ' + dimensionLabel + '\nMatched excerpts:\n' + uniqueExcerpts.join('\n')
      }
    ]
  };

  const resp = await fetch(STRICT_BASE_URL + '/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + process.env.OPENAI_API_KEY,
    },
    body: JSON.stringify(body),
  });

  if (!resp.ok) {
    throw new Error('API returned ' + resp.status + ': ' + (await resp.text()).slice(0, 200));
  }

  const data = await resp.json();
  const content = data.choices[0].message.content.trim();
  // Parse JSON from response, handling possible markdown code fences
  const jsonStr = content.replace(/^```json?\s*/i, '').replace(/\s*```$/i, '');
  const parsed = JSON.parse(jsonStr);
  return {
    multiplier: Math.max(0, Math.min(1, Number(parsed.multiplier) || 0)),
    reasoning: String(parsed.reasoning || ''),
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 3 — FILE I/O
// ─────────────────────────────────────────────────────────────────────────────

function readFile(filePath) {
  return fs.readFileSync(filePath, 'utf8');
}

function scanDir(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  return entries
    .filter(e => e.isFile() && path.extname(e.name).toLowerCase() === '.md')
    .map(e => path.resolve(dir, e.name))
    .sort();
}

function writeFile(filePath, data) {
  fs.writeFileSync(filePath, data, 'utf8');
}

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 4 — OUTPUT RENDERERS  (pure, return strings)
// ─────────────────────────────────────────────────────────────────────────────

const ANSI_RESET  = '\x1b[0m';
const ANSI_BOLD   = '\x1b[1m';
const ANSI_RED    = '\x1b[31m';
const ANSI_YELLOW = '\x1b[33m';
const ANSI_GREEN  = '\x1b[32m';

function colorize(score) {
  if (score <= COLOR_THRESHOLDS.red)    return ANSI_RED;
  if (score <= COLOR_THRESHOLDS.yellow) return ANSI_YELLOW;
  return ANSI_GREEN;
}

function renderBar(score, width) {
  if (width === undefined) width = 30;
  const filled = Math.round(score / 100 * width);
  const empty  = width - filled;
  const bar    = '\u2588'.repeat(filled) + '\u2591'.repeat(empty);
  return colorize(score) + bar + ANSI_RESET;
}

function renderEnergy(score) {
  const total = 5;
  if (score >= 70) {
    const filled = Math.min(total, Math.round(score / 100 * total));
    return '\uD83D\uDFE2'.repeat(filled) + '\u2B1B'.repeat(total - filled);
  } else if (score >= 40) {
    const filled = Math.round(score / 100 * total);
    return '\uD83D\uDFE1'.repeat(filled) + '\u2B1B'.repeat(total - filled);
  } else {
    const filled = Math.max(1, Math.round(score / 100 * total));
    return '\uD83D\uDD34'.repeat(filled) + '\u2B1B'.repeat(total - filled);
  }
}

function renderVerdict(score) {
  if (score >= 70) return '  \uD83D\uDFE2 Suit fully powered. Ready for Ridley.\n';
  if (score >= 50) return '  \u26A0\uFE0F  Suit incomplete. Visit the Chozo Statue.\n';
  return '  \uD83D\uDEA8 Critical energy. Major upgrades needed before deployment.\n';
}

const UPGRADE_NAMES = {
  roleClarity:         'POWER BEAM',
  constraintDensity:   'VARIA SUIT',
  hallucinationGuards: 'SCAN VISOR',
  outputSpecificity:   'AREA MAP',
  testability:         'LOCK-ON TARGETING',
  escapeHatches:       'ENERGY TANK',
};

function renderChozo(dimensions) {
  const low = DIMENSIONS
    .filter(d => dimensions[d.key] < 40)
    .sort((a, b) => dimensions[a.key] - dimensions[b.key]);
  if (low.length === 0) return '';

  const top = low[0];
  const upgradeName = UPGRADE_NAMES[top.key] || top.label.toUpperCase();

  let out = '\n';
  out += '      \u250C\u2500\u2500\u2500\u2510\n';
  out += '      \u2502 \u25C6 \u2502\n';
  out += '    \u250C\u2500\u2518   \u2514\u2500\u2510\n';
  out += '    \u2502 \u25C4\u2588\u2588\u2588\u25BA \u2502\n';
  out += '    \u2502  \u2571 \u2572  \u2502\n';
  out += '    \u2514\u2500\u2571   \u2572\u2500\u2518\n';
  out += '\n';
  out += '    \u266A da da da DA DAAAA \u266A\n';

  for (const dim of low) {
    const name = UPGRADE_NAMES[dim.key] || dim.label.toUpperCase();
    out += '    ' + dim.icon + ' ' + name + ' ACQUIRED\n';
  }
  out += '\n';

  return out;
}

function renderBarChart(result) {
  const { file, dimensions, composite: comp, wordCount, profile } = result;
  const name       = path.basename(file);
  const labelWidth = 26;
  const BAR_WIDTH  = 30;

  const profileTag = profile && profile !== 'balanced' ? '  profile: ' + profile : '';
  let out = '\n' + ANSI_BOLD + '\uD83D\uDD2C  agent-xray: ' + name + ANSI_RESET +
            '  (' + wordCount + ' words)' + profileTag + '\n\n';

  for (const { key, label, icon } of DIMENSIONS) {
    const score     = dimensions[key];
    const padLabel  = label.padEnd(labelWidth);
    const scoreStr  = String(score).padStart(3);
    let rawTag = '';
    if (result.rawScores && result.rawScores[key] !== score) {
      rawTag = ' (' + result.rawScores[key] + '\u2192' + score + ')';
    }
    out += '  ' + icon + ' ' + padLabel + ' [' + scoreStr + '] ' + renderBar(score, BAR_WIDTH) + '  ' + renderEnergy(score) + rawTag + '\n';
  }

  const divider = '\u2500'.repeat(labelWidth + BAR_WIDTH + 16);
  out += '  ' + divider + '\n';
  out += '  \uD83D\uDD2C  ' + 'Composite'.padEnd(labelWidth) +
         ' [' + String(comp).padStart(3) + '] ' +
         renderBar(comp, BAR_WIDTH) + '  ' + renderEnergy(comp) + '\n\n';

  out += renderVerdict(comp);

  if (comp < 50) {
    out += renderChozo(dimensions);
  }

  return out;
}

function renderTable(results) {
  if (!results || results.length === 0) return '';

  const sorted = [...results].sort((a, b) => b.composite - a.composite);

  // Column widths
  const CW = { rank: 3, file: 28, dim: 5, comp: 4 };

  // Build separator using ─┼─ between cells
  const allWidths = [CW.rank, CW.file, CW.dim, CW.dim, CW.dim, CW.dim, CW.dim, CW.dim, CW.comp];
  const sep = allWidths.map(w => '─'.repeat(w)).join('─┼─');

  // Header row
  const headers = [
    'Rank'.padStart(CW.rank),
    'File'.padEnd(CW.file),
    'Role'.padStart(CW.dim),
    'Constraint'.padStart(CW.dim),
    'Hallucination'.padStart(CW.dim),
    'Output'.padStart(CW.dim),
    'Testability'.padStart(CW.dim),
    'Escape'.padStart(CW.dim),
    'Composite'.padStart(CW.comp),
  ];

  let out = '\n' + headers.join(' │ ') + '\n' + sep + '\n';

  // Data rows
  sorted.forEach((r, i) => {
    const d    = r.dimensions;
    const name = path.basename(r.file);
    const truncName = name.length > CW.file ? name.slice(0, CW.file - 1) + '…' : name;
    const compStr = colorize(r.composite) + String(r.composite).padStart(CW.comp) + ANSI_RESET;

    const cells = [
      String(i + 1).padStart(CW.rank),
      truncName.padEnd(CW.file),
      String(d.roleClarity).padStart(CW.dim),
      String(d.constraintDensity).padStart(CW.dim),
      String(d.hallucinationGuards).padStart(CW.dim),
      String(d.outputSpecificity).padStart(CW.dim),
      String(d.testability).padStart(CW.dim),
      String(d.escapeHatches).padStart(CW.dim),
      compStr,
    ];
    out += cells.join(' │ ') + '\n';
  });

  out += sep + '\n';

  // Fleet mean footer
  const mean      = sorted.reduce((s, r) => s + r.composite, 0) / sorted.length;
  const meanScore = Math.round(mean);
  const meanStr   = String(meanScore);
  const meanComp  = colorize(meanScore) + meanStr.padStart(CW.comp) + ANSI_RESET;

  const footerCells = [
    ''.padStart(CW.rank),
    'MEAN'.padEnd(CW.file),
    ''.padStart(CW.dim),
    ''.padStart(CW.dim),
    ''.padStart(CW.dim),
    ''.padStart(CW.dim),
    ''.padStart(CW.dim),
    ''.padStart(CW.dim),
    meanComp,
  ];
  out += footerCells.join(' │ ') + '\n\n';

  return out;
}

function renderBadge(score, label) {
  if (label === undefined) label = 'agent-xray';
  const rightText = String(score);
  const CHAR_W    = 6.5;
  const PADDING   = 10;
  const leftW     = Math.round(label.length * CHAR_W) + PADDING;
  const rightW    = Math.round(rightText.length * CHAR_W) + PADDING;
  const totalW    = leftW + rightW;
  const color     = score >= 70 ? '#44cc11' : score >= 40 ? '#dfb317' : '#e05d44';

  // Text positions (SVG units = pixels * 10 due to scale(.1))
  const leftCenter  = Math.round(leftW  / 2 * 10);
  const rightCenter = Math.round((leftW + rightW / 2) * 10);
  const leftTL      = Math.max(1, Math.round((label.length    * CHAR_W - 2) * 10));
  const rightTL     = Math.max(1, Math.round((rightText.length * CHAR_W - 2) * 10));

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${totalW}" height="20" role="img" aria-label="${label}: ${score}">
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="${totalW}" height="20" rx="3" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="${leftW}" height="20" fill="#555"/>
    <rect x="${leftW}" width="${rightW}" height="20" fill="${color}"/>
    <rect width="${totalW}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="110">
    <text aria-hidden="true" x="${leftCenter}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="${leftTL}" lengthAdjust="spacing">${label}</text>
    <text x="${leftCenter}" y="140" transform="scale(.1)" textLength="${leftTL}" lengthAdjust="spacing">${label}</text>
    <text aria-hidden="true" x="${rightCenter}" y="150" fill="#010101" fill-opacity=".3" transform="scale(.1)" textLength="${rightTL}" lengthAdjust="spacing">${rightText}</text>
    <text x="${rightCenter}" y="140" transform="scale(.1)" textLength="${rightTL}" lengthAdjust="spacing">${rightText}</text>
  </g>
</svg>`;
}

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 5 — CLI
// ─────────────────────────────────────────────────────────────────────────────

function parseArgs(argv) {
  const args = argv.slice(2);

  if (args.length === 0 || args.includes('--help')) {
    return { mode: 'help', target: null, outfile: null, jsonMode: false, profile: 'balanced', strictMode: false };
  }

  const result = { mode: 'single', target: null, outfile: null, jsonMode: false, profile: 'balanced', strictMode: false };

  if (args.includes('--json')) result.jsonMode = true;
  if (args.includes('--strict')) result.strictMode = true;

  // Track indices consumed as flag values so they are not treated as positionals
  const flagValueIdxs = new Set();

  if (args.includes('--badge')) {
    const idx = args.indexOf('--badge');
    const val = args[idx + 1];
    if (!val || val.startsWith('--')) {
      process.stderr.write('Error: --badge requires a path argument.\n');
      process.exit(1);
    }
    result.outfile = val;
    flagValueIdxs.add(idx + 1);
  }

  if (args.includes('--profile')) {
    const idx = args.indexOf('--profile');
    const val = args[idx + 1];
    if (!val || val.startsWith('--')) {
      process.stderr.write('Error: --profile requires a name argument.\nAvailable: ' + Object.keys(PROFILES).join(', ') + '\n');
      process.exit(1);
    }
    if (!PROFILES[val]) {
      process.stderr.write('Error: Unknown profile "' + val + '". Available: ' + Object.keys(PROFILES).join(', ') + '\n');
      process.exit(1);
    }
    result.profile = val;
    flagValueIdxs.add(idx + 1);
  }

  if (args.includes('--self-test')) {
    result.mode = 'self-test';
    return result;
  }

  if (args.includes('--fleet')) {
    const idx = args.indexOf('--fleet');
    let val = null;
    let valIdx = -1;
    for (let i = idx + 1; i < args.length; i++) {
      if (!args[i].startsWith('--')) { val = args[i]; valIdx = i; break; }
    }
    if (!val) {
      process.stderr.write('Error: --fleet requires a directory argument.\n');
      process.exit(1);
    }
    result.mode   = 'fleet';
    result.target = val;
    flagValueIdxs.add(valIdx);
    return result;
  }

  // Single file mode — first non-flag positional argument (not a flag value)
  const positional = args.find((a, i) => !a.startsWith('--') && !flagValueIdxs.has(i));
  if (!positional) {
    printHelp();
    process.exit(0);
  }
  result.target = positional;

  // Reject unknown flags
  const knownFlags = new Set(['--json', '--badge', '--fleet', '--self-test', '--help', '--profile', '--strict']);
  for (const arg of args) {
    if (arg.startsWith('--') && !knownFlags.has(arg)) {
      process.stderr.write('Error: Unknown flag: ' + arg + '\n');
      printHelp();
      process.exit(1);
    }
  }

  return result;
}

async function dispatch(opts) {
  const { mode, target, outfile, jsonMode, profile, strictMode } = opts;
  const scoreOpts = { profile };

  if (mode === 'help') {
    printHelp();
    return;
  }

  // Validate --strict requirements
  let useStrict = strictMode;
  if (useStrict && !process.env.OPENAI_API_KEY) {
    process.stderr.write('Warning: --strict requires OPENAI_API_KEY env var. Falling back to normal mode.\n');
    useStrict = false;
  }

  // ── Single file mode ──────────────────────────────────────────────────────
  if (mode === 'single') {
    let text;
    try {
      text = readFile(target);
    } catch (e) {
      process.stderr.write('Error: Cannot read file "' + target + '": ' + e.message + '\n');
      process.exit(1);
    }

    const result = scoreText(text, target, scoreOpts);

    // Apply --strict LLM evaluation if enabled
    if (useStrict) {
      result.rawScores = { ...result.dimensions };
      result.strictReasoning = {};
      for (const { key, label } of DIMENSIONS) {
        if (result.dimensions[key] > 0 && result.excerpts[key].length > 0) {
          try {
            const evaluation = await strictEvaluate(label, result.excerpts[key]);
            result.strictReasoning[key] = evaluation.reasoning;
            result.dimensions[key] = Math.round(result.dimensions[key] * evaluation.multiplier);
          } catch (e) {
            process.stderr.write('Warning: --strict evaluation failed for ' + label + ': ' + e.message + '\n');
            result.strictReasoning[key] = 'evaluation failed — using raw score';
          }
        }
      }
      result.composite = composite(result.dimensions, profile);
    }

    if (jsonMode) {
      const jsonOut = {
        file:      result.file,
        scores:    result.dimensions,
        composite: result.composite,
        profile:   result.profile,
      };
      if (useStrict) {
        jsonOut.rawScores = result.rawScores;
        jsonOut.strictReasoning = result.strictReasoning;
      }
      process.stdout.write(JSON.stringify(jsonOut, null, 2) + '\n');
    } else {
      process.stdout.write(renderBarChart(result));
    }

    if (outfile) {
      try {
        writeFile(outfile, renderBadge(result.composite));
      } catch (e) {
        process.stderr.write('Error: Cannot write badge to "' + outfile + '": ' + e.message + '\n');
        process.exit(1);
      }
    }
    return;
  }

  // ── Fleet / self-test mode ────────────────────────────────────────────────
  if (mode === 'fleet' || mode === 'self-test') {
    const dir = mode === 'self-test' ? SELF_TEST_DIR : target;

    let files;
    try {
      files = scanDir(dir);
    } catch (e) {
      process.stderr.write('Error: Cannot scan directory "' + dir + '": ' + e.message + '\n');
      process.exit(1);
    }

    if (files.length === 0) {
      process.stderr.write('Warning: No .md files found in "' + dir + '".\n');
      process.exit(1);
    }

    const results = [];
    for (const f of files) {
      try {
        results.push(scoreText(readFile(f), f, scoreOpts));
      } catch (e) {
        process.stderr.write('Warning: Could not read "' + f + '": ' + e.message + '\n');
      }
    }

    const sorted = [...results].sort((a, b) => b.composite - a.composite);

    if (jsonMode) {
      process.stdout.write(JSON.stringify(
        sorted.map(r => ({ file: r.file, scores: r.dimensions, composite: r.composite, profile: r.profile })),
        null, 2
      ) + '\n');
    } else {
      const profileTag = profile !== 'balanced' ? '  (profile: ' + profile + ')' : '';
      process.stdout.write('agent-xray Fleet Report: ' + dir + profileTag + '\n');
      process.stdout.write(renderTable(sorted));
    }

    if (outfile) {
      // Fleet badge mode: outfile is a directory
      for (const r of sorted) {
        const base      = path.basename(r.file, '.md');
        const badgePath = path.join(outfile, base + '.svg');
        try {
          writeFile(badgePath, renderBadge(r.composite, path.basename(r.file)));
        } catch (e) {
          process.stderr.write('Warning: Could not write badge "' + badgePath + '": ' + e.message + '\n');
        }
      }
    }

    if (mode === 'self-test') {
      const anyLow = sorted.some(r => r.composite < 50);
      process.exit(anyLow ? 1 : 0);
    }
    return;
  }
}

function printHelp() {
  process.stdout.write(
    'agent-xray — Scan your AI agent\'s prompt. See what\'s missing.\n' +
    '\n' +
    'Usage:\n' +
    '  node agent-xray.js <file.md>                     Score a single prompt file\n' +
    '  node agent-xray.js --fleet <dir>                 Score all .md files in directory\n' +
    '  node agent-xray.js --self-test                   Run fleet mode on ~/.copilot/agents/\n' +
    '  node agent-xray.js --badge <outfile>             Write SVG badge (single file mode)\n' +
    '  node agent-xray.js --fleet <dir> --badge <dir>   Write one SVG badge per file into dir\n' +
    '  node agent-xray.js --json                        Emit scores as JSON to stdout\n' +
    '  node agent-xray.js --profile <name>              Use a scoring profile (default: balanced)\n' +
    '  node agent-xray.js --strict                      LLM-evaluate keyword quality (needs OPENAI_API_KEY)\n' +
    '  node agent-xray.js --help                        Show this help\n' +
    '\n' +
    'Profiles:\n' +
    '  balanced   — Equal weights across all dimensions (default)\n' +
    '  security   — Emphasizes hallucination guards (2x) and constraints (1.5x)\n' +
    '  creative   — Emphasizes role clarity (1.5x), relaxes constraints (0.5x)\n' +
    '  ci-gate    — Emphasizes testability (2x) for CI pipeline gates\n' +
    '  assistant  — Emphasizes role clarity (1.5x) and hallucination guards (1.5x)\n' +
    '\n' +
    'Scoring Dimensions:\n' +
    '  Role Clarity             — Explicit persona, scope, and responsibility statements\n' +
    '  Constraint Density       — Prohibitions, limits, and boundary definitions\n' +
    '  Hallucination Guardrails — Source citation, uncertainty expressions, refusals\n' +
    '  Output Specificity       — Format, length, structure, or schema definitions\n' +
    '  Testability              — Example inputs/outputs, acceptance criteria\n' +
    '  Escape Hatches           — Instructions for out-of-scope conditions\n' +
    '\n' +
    'Score Colors:\n' +
    '  \x1b[31mRed    (0–39)\x1b[0m   — Needs significant improvement\n' +
    '  \x1b[33mYellow (40–69)\x1b[0m  — Acceptable, room for improvement\n' +
    '  \x1b[32mGreen  (70–100)\x1b[0m — Well-specified\n' +
    '\n' +
    'Environment Variables (for --strict):\n' +
    '  OPENAI_API_KEY    — Required. Your OpenAI-compatible API key\n' +
    '  OPENAI_BASE_URL   — Optional. API base URL (default: https://api.openai.com/v1)\n' +
    '  OPENAI_MODEL      — Optional. Model name (default: gpt-4o-mini)\n' +
    '\n' +
    'Exit Codes:\n' +
    '  0 — Success (or all agents >= 50 in --self-test mode)\n' +
    '  1 — Error, or any agent < 50 in --self-test mode\n'
  );
}

async function main() {
  try {
    const opts = parseArgs(process.argv);
    await dispatch(opts);
  } catch (e) {
    process.stderr.write('Unexpected error: ' + e.message + '\n');
    process.exit(1);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// SECTION 6 — ENTRY POINT
// ─────────────────────────────────────────────────────────────────────────────

module.exports = {
  DIMENSIONS,
  HEURISTICS,
  PROFILES,
  COLOR_THRESHOLDS,
  SELF_TEST_DIR,
  getSentences,
  densityMultiplier,
  applyHeuristics,
  scoreDimension,
  scoreText,
  composite,
  strictEvaluate,
  readFile,
  scanDir,
  writeFile,
  colorize,
  renderBar,
  renderEnergy,
  renderChozo,
  renderBarChart,
  renderTable,
  renderBadge,
  parseArgs,
  printHelp,
};

if (require.main === module) main();
