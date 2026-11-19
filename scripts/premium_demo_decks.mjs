// Static showcase decks. Requires actual approved images and computed SVGs.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, v, i, a) => {
  if (v.startsWith('--')) pairs.push([v.slice(2), a[i + 1]]);
  return pairs;
}, []));
for (const key of ['manifest', 'runtime-modules', 'runtime-python', 'skill-dir', 'workspace']) {
  if (!args[key]) throw new Error(`Missing --${key}`);
}
const runtimePackage = path.join(args['runtime-modules'], '@oai/artifact-tool');
process.env.RUNTIME_NODE_MODULES = args['runtime-modules'];
const packageMeta = JSON.parse(await fs.readFile(path.join(runtimePackage, 'package.json'), 'utf8'));
const { Presentation, PresentationFile } = await import(pathToFileURL(
  path.join(runtimePackage, packageMeta.exports['.'])).href);
const { finalizePresentation } = await import(pathToFileURL(
  path.join(args['skill-dir'], 'container_tools/artifact_tool_utils.mjs')).href);
const manifest = JSON.parse(await fs.readFile(args.manifest, 'utf8'));
const work = path.resolve(args.workspace);
const build = path.join(work, 'build');
const output = path.join(work, 'output');
await fs.mkdir(build, { recursive: true });
await fs.mkdir(output, { recursive: true });

function text(slide, value, rect, size, color, family, bold = false) {
  const s = slide.shapes.add({geometry: 'textbox', position: rect, fill: 'none',
    line: {fill: 'none', width: 0}});
  s.text = value;
  s.text.style = {fontSize: size, color, typeface: family, bold, autoFit: 'none'};
  return s;
}
function rect(slide, box, color) {
  slide.shapes.add({geometry: 'rect', position: box, fill: color,
    line: {fill: 'none', width: 0}});
}
for (const deck of manifest.decks) {
  if (args.only && args.only !== deck.case) continue;
  const p = Presentation.create({slideSize: {width: 1920, height: 1080}});
  const humanities = deck.case === 'humanities';
  const stem = humanities ? '山居秋暝_插画分镜课件' : '直线与圆_几何课件';
  const font = humanities ? 'KaiTi' : 'Microsoft YaHei';
  for (const [index, page] of deck.pages.entries()) {
    const slide = p.slides.add();
    slide.background.fill = humanities ? '#101F22' : '#08151F';
    if (page.svg) {
      const svg = await fs.readFile(page.svg, 'utf8');
      slide.images.add({svg, alt: page.title, position: {left: 0, top: 0, width: 1920, height: 1080}, fit: 'contain'});
      rect(slide, {left: 0, top: 0, width: 1920, height: 190}, '#08151F');
      text(slide, page.title, {left: 80, top: 42, width: 1740, height: 85}, 54, '#EDF5F7', font, true);
      text(slide, page.keywords.join('    '), {left: 82, top: 126, width: 1740, height: 44}, 29, '#A5BAC7', font);
      rect(slide, {left: 0, top: 998, width: 1920, height: 82}, '#08151F');
      text(slide, 'Lively Class', {left: 80, top: 1020, width: 220, height: 36}, 24, '#A5BAC7', font);
      text(slide, (page.visual === 'textbook_example' ? '教材原例' : '教学示意') + '   人教数学 九年级上册 · 148—149页',
        {left: 730, top: 1020, width: 1000, height: 36}, 22, '#A5BAC7', font);
    } else {
      if (!page.image) throw new Error('A slide has no actual selected artwork');
      const blob = new Uint8Array(await fs.readFile(page.image));
      slide.images.add({blob, contentType: 'image/png', alt: page.title,
        position: {left: 0, top: 12, width: 1920, height: 1056}, fit: 'contain'});
      // A clean caption panel leaves the approved artwork undistorted.
      rect(slide, {left: 0, top: 900, width: 1920, height: 180}, '#102226');
      const label = page.poem_line || page.title;
      text(slide, label, {left: 94, top: 917, width: 1730, height: 90}, 66, '#F4F2E6', font);
      text(slide, page.poem_line ? '王维 · 山居秋暝' : page.keywords.join('    '),
        {left: 98, top: 1015, width: 1540, height: 40}, 27, '#BACDCA', 'Microsoft YaHei');
    }
    text(slide, `${String(index + 1).padStart(2, '0')}`, {left: 1790, top: 1016, width: 70, height: 34},
      25, '#A5BAC7', 'Microsoft YaHei');
    slide.speakerNotes.text = `${page.source_note}\n\n原文完整讲者备注：\n${page.source_text}`;
    const png = await p.export({slide, format: 'png', scale: 1});
    const dir = path.join(build, deck.case + '-previews');
    await fs.mkdir(dir, {recursive: true});
    await fs.writeFile(path.join(dir, `${String(index + 1).padStart(2, '0')}.png`), new Uint8Array(await png.arrayBuffer()));
  }
  const candidate = path.join(build, `${stem}.draft.pptx`);
  await (await PresentationFile.exportPptx(p)).save(candidate);
  const finalPath = path.join(output, `${stem}.pptx`);
  const result = await finalizePresentation({workspaceDir: work, candidatePath: candidate, finalPath,
    explicitTotalSlideCount: deck.pages.length,
    pythonExecutable: args['runtime-python'],
    integrityValidatorPath: path.join(args['skill-dir'], 'container_tools/inspect_presentation_package_integrity.py'),
    layoutValidatorPath: path.join(args['skill-dir'], 'container_tools/inspect_presentation_layout_geometry.py'),
    layoutArgs: ['--expected-slide-size-emu', '18288000,10287000', '--validate-heading-fit'],
    fontPolicy: {basis: 'design', families: humanities ? ['KaiTi', 'Microsoft YaHei'] : ['Microsoft YaHei']},
    verifyArtifactToolImport: true,
    receiptPath: path.join(build, `${stem}.validation.json`)});
  console.log(JSON.stringify({case: deck.case, slides: deck.pages.length, finalPath, result}));
}
