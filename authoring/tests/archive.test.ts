import test from 'node:test';
import assert from 'node:assert/strict';
import {zipEntries,sha256} from '../src/archive.ts';
test('archive uses standard local/central signatures and rejects paths',()=>{
 const content=zipEntries({'project.json':'{"title":"ok"}','authoring-lyrics.lrc':'[00:00.000]Hello\n'});
 assert.equal(new DataView(content.buffer).getUint32(0,true),0x04034b50);
 assert(content.length>90);
 assert.equal(new DataView(content.buffer).getUint32(content.length-22,true),0x06054b50);
 assert.throws(()=>zipEntries({'../escape':'bad'}));
});
test('SHA256 is standards-based and deterministic',async()=>{
 assert.equal(await sha256('abc'),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
});
