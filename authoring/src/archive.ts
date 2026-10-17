/** Portable ZIP (STORE) writer; no dependency or compression, CRC-32 validated.
 * Only exports newly-authored sidecars, project.json and instructions. Original
 * audio, model weights, PNGs, and SHA-pinned project inputs are NOT duplicated.
 */
const enc = new TextEncoder();
const crcTable = new Uint32Array(256);
for(let n=0;n<256;n++) {
 let c=n;for(let k=0;k<8;k++)c=c&1?0xedb88320^(c>>>1):c>>>1;
 crcTable[n]=c>>>0;
}
function crc32(data:Uint8Array):number {
 let c=0xffffffff;for(const b of data)c=crcTable[(c^b)&255]^(c>>>8);return (c^0xffffffff)>>>0;
}
function p16(n:number) { return [n&255,(n>>>8)&255]; }
function p32(n:number) { return [n&255,(n>>>8)&255,(n>>>16)&255,(n>>>24)&255]; }
export function zipEntries(rows: Record<string,string>): Uint8Array {
 const names=Object.keys(rows).sort();
 if(!names.length || names.length>128)throw Error('Archive requires 1..128 entries');
 const local:Uint8Array[]=[],central:Uint8Array[]=[];
 let offset=0,centralLength=0;
 for(const name of names){
  if(!name || name.length>190 || name.startsWith('/') || name.includes('\\') ||
     name.split('/').includes('..') || !/^[\w\-./]+$/.test(name))throw Error('Unsafe archive entry');
  const nameBytes=enc.encode(name),content=enc.encode(rows[name]);
  if(content.length>4*1024*1024)throw Error('Archive sidecar exceeds 4 MiB');
  const checksum=crc32(content),flags=0x0800,method=0;
  const header=[...p32(0x04034b50),...p16(20),...p16(flags),...p16(method),...p16(0),...p16(0),
    ...p32(checksum),...p32(content.length),...p32(content.length),...p16(nameBytes.length),...p16(0)];
  const localHead=Uint8Array.from([...header,...nameBytes]);
  local.push(localHead,content);
  const centralHeader=Uint8Array.from([...p32(0x02014b50),...p16(20),...p16(20),...p16(flags),...p16(method),...p16(0),...p16(0),
    ...p32(checksum),...p32(content.length),...p32(content.length),...p16(nameBytes.length),...p16(0),
    ...p16(0),...p16(0),...p16(0),...p32(0),...p32(offset),...nameBytes]);
  central.push(centralHeader);
  centralLength+=centralHeader.length;
  offset+=localHead.length+content.length;
 }
 const tail=Uint8Array.from([...p32(0x06054b50),...p16(0),...p16(0),...p16(names.length),...p16(names.length),
   ...p32(centralLength),...p32(offset),...p16(0)]);
 const out=new Uint8Array(offset+centralLength+tail.length);
 let cursor=0;
 for(const part of [...local,...central,tail]) {out.set(part,cursor);cursor+=part.length;}
 return out;
}
function hex(buf:ArrayBuffer):string{return [...new Uint8Array(buf)].map(b=>b.toString(16).padStart(2,'0')).join('');}
export async function sha256(text:string):Promise<string> {
 const data=enc.encode(text);
 if(!globalThis.crypto?.subtle)throw Error('WebCrypto SHA-256 is required for authoring pinned sidecars');
 return hex(await globalThis.crypto.subtle.digest('SHA-256',data));
}
