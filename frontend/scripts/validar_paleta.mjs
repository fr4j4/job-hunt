// Falla el build si cambia un hex y la paleta deja de pasar los chequeos (claro y oscuro).
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { PALETA, SUPERFICIE, ORDINAL } from '../src/viz/paleta.mjs';

const script = fileURLToPath(new URL('./validate_palette.js', import.meta.url));
let fallo = false;
const correr = (titulo, args) => {
  const r = spawnSync('node', [script, ...args], { encoding: 'utf8' });
  const salida = (r.stdout || '') + (r.stderr || '');
  const lineas = salida.split('\n').filter((l) => /FAIL|WARN|ALL CHECKS/.test(l));
  console.log(`\n# ${titulo}\n${lineas.join('\n')}`);
  if (r.status !== 0 || /\[FAIL\]/.test(salida)) fallo = true;
};
for (const modo of ['light', 'dark']) {
  correr(`categórica ${modo} (8 slots, adyacentes)`,
    [PALETA[modo].join(','), '--mode', modo, '--surface', SUPERFICIE[modo]]);
  correr(`categórica ${modo} (3 primeros, todos-contra-todos)`,
    [PALETA[modo].slice(0, 3).join(','), '--mode', modo, '--surface', SUPERFICIE[modo], '--pairs', 'all']);
  correr(`ordinal ${modo}`, [ORDINAL[modo].join(','), '--ordinal', '--mode', modo, '--surface', SUPERFICIE[modo]]);
}
process.exit(fallo ? 1 : 0);
