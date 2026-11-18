/** Real authoring operations and export bytes consumed by native qualification.
 * Run with Node 24; no browser, renderer imitation, or additional dependency.
 */
import {readFileSync} from 'node:fs';
import {strict as assert} from 'node:assert';
import {zipEntries} from '../src/archive.ts';
import {
  withInitialEffectGraph, withEffectSource, withEffectNode, withEffectOpacity,
  withEffectInputs, withEffectConnection, withEffectProgress,
} from '../src/graphEditor.ts';
import type {Document} from '../src/model.ts';

const input=JSON.parse(readFileSync(0,'utf8')) as {base:Document;controlled:Document};
const before=JSON.stringify(input);
const cases:{name:string;original:'base'|'controlled';archive:string}[]=[];
function save(name:string,project:Document,original:'base'|'controlled'='base') {
  const archive=zipEntries({
    'project.json':JSON.stringify(project,null,2)+'\n',
    'AUTHORING-README.txt':'Synthetic M10.2 qualification; source assets remain checksum-pinned beside the project.\n',
  });
  cases.push({name,original,archive:Buffer.from(archive).toString('base64')});
}
const identity=withInitialEffectGraph(input.base);
save('identity',identity);
let sources=identity;
for(const source of ['layer:red','layer:blue','layer:mask'])sources=withEffectSource(sources,source);
let forks=withEffectNode(sources,'opacity',['layer:red'],{makeOutput:false});
forks=withEffectOpacity(forks,'editor-opacity-1',.5);
forks=withEffectNode(forks,'opacity',['layer:blue'],{makeOutput:false});
forks=withEffectOpacity(forks,'editor-opacity-2',.75);
assert.equal(forks.effect_graph.output,'frame');
const merged=withEffectNode(forks,'composite',['editor-opacity-1','editor-opacity-2']);
save('fork_merge',merged);
save('merge_reordered',withEffectInputs(merged,'editor-composite-1',['editor-opacity-2','editor-opacity-1']));
save('rewired',withEffectConnection(merged,'editor-composite-1',1,'layer:red'));
const matte=withEffectNode(sources,'matte',['layer:red','layer:mask']);
save('matte',matte);
const multimatte=withEffectInputs(matte,'editor-matte-1',['layer:red','layer:mask','layer:blue']);
save('multi_matte',multimatte);
assert.deepEqual(withEffectInputs(multimatte,'editor-matte-1',['layer:red','layer:mask']),matte);
const transition=withEffectNode(sources,'transition',['layer:red','layer:blue']);
save('transition',withEffectProgress(transition,'editor-transition-1',.35));
save('transition_start',withEffectProgress(transition,'editor-transition-1',0));
save('transition_end',withEffectProgress(transition,'editor-transition-1',1));
save('matte_transition',withEffectNode(sources,'matte_transition',['layer:red','layer:blue','layer:mask']));
save('control_preserving_rewire',withEffectConnection(input.controlled,'control-branch',0,'layer:blue'),'controlled');
assert.equal(JSON.stringify(input),before,'Authoring must never mutate the imported project');
process.stdout.write(JSON.stringify({schema:'framerudiments.authoring-export-fixtures.v1',cases}));
