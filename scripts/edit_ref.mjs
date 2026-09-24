import ZAI from 'z-ai-web-dev-sdk';
import fs from 'fs';

const [,, inPath, outPath, prompt, size] = process.argv;
const b64 = fs.readFileSync(inPath).toString('base64');

const zai = await ZAI.create();
const response = await zai.images.generations.edit({
  prompt,
  images: [{ url: `data:image/png;base64,${b64}` }],
  size: size || '768x1344'
});

const out = response?.data?.[0]?.base64;
if (!out) throw new Error('no image returned: ' + JSON.stringify(response).slice(0, 300));
fs.writeFileSync(outPath, Buffer.from(out, 'base64'));
console.log('saved', outPath);
