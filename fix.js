const fs = require('fs');
const path = require('path');

const ROOT = process.argv[2] || '.';

// extensions à traiter
const EXTENSIONS = /\.(js|ts|tsx|json|md|html|css)$/i;

// détecte du mojibake classique
function looksBroken(text) {
  return /Ã.|â€|Â./.test(text);
}

// fix 1 passe
function fixOnce(t) {
  return Buffer.from(t, 'latin1').toString('utf8');
}

// fix multi-pass intelligent
function fixText(text) {
  let prev;
  let current = text;

  for (let i = 0; i < 5; i++) {
    prev = current;
    current = fixOnce(current);
    if (current === prev) break;
  }

  return current;
}

function processFile(file) {
  const original = fs.readFileSync(file, 'utf8');

  if (!looksBroken(original)) return; // skip fichiers clean

  const fixed = fixText(original);

  if (fixed === original) return; // rien changé

  const backup = file + '.bak';

  // backup si pas déjà existant
  if (!fs.existsSync(backup)) {
    fs.copyFileSync(file, backup);
    console.log('💾 backup:', backup);
  }

  fs.writeFileSync(file, fixed, 'utf8');
  console.log('✅ fixed:', file);
}

function walk(dir) {
  for (const entry of fs.readdirSync(dir)) {
    const full = path.join(dir, entry);

    if (fs.statSync(full).isDirectory()) {
      if (entry === 'node_modules' || entry.startsWith('.git')) continue;
      walk(full);
    } else {
      if (EXTENSIONS.test(full)) {
        processFile(full);
      }
    }
  }
}

console.log('🚀 Starting fix in:', ROOT);
walk(ROOT);
console.log('🎉 Done');