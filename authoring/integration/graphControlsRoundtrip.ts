/** M10.3 actual native HTTP proposals, bounded client acceptance and ZIP export. */
import {readFileSync} from 'node:fs';
import {strict as assert} from 'node:assert';
import {zipEntries} from '../src/archive.ts';
import {
  GraphControlFence, parseGraphEditResponse, readGraphControlResponse,
  setGraphParameter, putGraphBinding, removeGraphBinding, moveGraphBinding,
} from '../src/graphControls.ts';
import type {GraphControlCatalog, GraphEditOperation} from '../src/graphControls.ts';
import type {Document} from '../src/model.ts';

interface Snapshot {project:Document;sidecars:Document;}
interface Plan extends Document {kind:string;}
interface Fixture {name:string;project:Document;node:string;operations:Plan[];}
const input=JSON.parse(readFileSync(0,'utf8')) as {origin:string;cases:Fixture[]};
const before=JSON.stringify(input);
const cases:Document[]=[];
let requests=0;

async function request(route:string,body:Document):Promise<unknown>{
  requests++;
  const response=await fetch(`${input.origin}/api/graph/${route}`,{
    method:'POST',headers:{'Content-Type':'application/json','Origin':input.origin},
    body:JSON.stringify(body),signal:AbortSignal.timeout(30000),
  });
  const result=await readGraphControlResponse(response);
  assert.equal(response.status,200,JSON.stringify(result));
  return result;
}

function operation(catalog:GraphControlCatalog,plan:Plan):GraphEditOperation{
  if(plan.kind==='set_parameter')return setGraphParameter(catalog,plan.property,plan.value);
  if(plan.kind==='remove_binding')return removeGraphBinding(catalog,plan.index);
  if(plan.kind==='move_binding')return moveGraphBinding(catalog,plan.index,plan.direction);
  assert.equal(plan.kind,'put_binding');
  const motion=catalog.motions.find(row=>row.kind===plan.motion.kind&&
    (plan.motion.pattern===undefined||row.motion.pattern===plan.motion.pattern)&&
    (row.kind!=='named'||Object.keys(row.motion).length===2));
  assert.ok(motion,'Native source catalogue must contain the requested released or pinned motion');
  return putGraphBinding(catalog,{index:plan.index,property:plan.property,motion_id:motion.id,
    settings:plan.settings,...(plan.motion_settings?{motion_settings:plan.motion_settings}:{})});
}

for(const fixture of input.cases){
  const original=JSON.stringify(fixture.project);
  let snapshot:Snapshot={project:fixture.project,sidecars:{}};
  const fence=new GraphControlFence<Snapshot>();
  const discovery=fence.begin(snapshot,fixture.node);
  let catalog=fence.acceptCatalog(await request('catalog',{
    project:snapshot.project,sidecars:snapshot.sidecars,node:fixture.node,
  }),discovery);
  assert.ok(catalog,'Actual native catalogue must pass the bounded TypeScript parser');
  const parameters=catalog.parameters.length;
  let rejectedStale=0;
  for(const planned of fixture.operations){
    const edit=operation(catalog,planned);
    const ticket=fence.begin(snapshot,fixture.node,catalog.request_sha256);
    const raw=await request('edit',{project:snapshot.project,sidecars:snapshot.sidecars,
      node:fixture.node,operation:edit});
    const response=parseGraphEditResponse(raw);
    assert.ok(response,'Actual native proposal must pass bounded parsing');
    const next=fence.acceptEdit(response,ticket,snapshot.project,edit);
    assert.ok(next,'A matching native proposal must apply atomically');
    assert.notEqual(next,snapshot.project);
    fence.cancel();
    assert.equal(fence.acceptEdit(response,ticket,snapshot.project,edit),null);
    rejectedStale++;
    snapshot={project:next,sidecars:snapshot.sidecars};
    catalog=response.catalog;
  }
  assert.equal(JSON.stringify(fixture.project),original,'Imported project must remain byte-equivalent JSON');
  const archive=zipEntries({
    'project.json':JSON.stringify(snapshot.project,null,2)+'\n',
    'AUTHORING-README.txt':'Synthetic M10.3 native parameter/control editor qualification.\n',
  });
  cases.push({name:fixture.name,archive:Buffer.from(archive).toString('base64'),
    parameters,operations:fixture.operations.length,rejected_stale_responses:rejectedStale});
}
assert.equal(JSON.stringify(input),before,'No fixture input may be mutated');
process.stdout.write(JSON.stringify({schema:'framerudiments.authoring-controls-export.v1',requests,cases}));
