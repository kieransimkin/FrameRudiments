import type {Document, Sidecars} from './model.ts';

/** A complete browser draft. Source pins and clocks are ordinary retained JSON. */
export interface AuthoringSnapshot {
 readonly project:Document;
 readonly sidecars:Sidecars;
 readonly dirty:Readonly<Record<'lines'|'words'|'cues',boolean>>;
}

/** Use this object, not current, as the revision for asynchronous work. */
export interface AuthoringHistory {
 readonly current:AuthoringSnapshot;
 readonly past:readonly AuthoringSnapshot[];
 readonly future:readonly AuthoringSnapshot[];
}

export const AUTHORING_HISTORY_LIMIT=100;
export interface AuthoringCommit {
 readonly history:AuthoringHistory;
 readonly disposition:'committed'|'unchanged'|'stale';
}

function freezeData<T>(value:T,visiting=new WeakSet<object>()):T {
 if(value===null||typeof value!=='object')return value;
 // Only plain authoring data has an immutable state after Object.freeze.
 // Map/Set/Date and other mutable internal slots are not project JSON.
 const prototype=Object.getPrototypeOf(value);
 if(!Array.isArray(value)&&prototype!==Object.prototype&&prototype!==null)
   throw TypeError('Authoring snapshots require plain data objects and arrays');
 if(visiting.has(value))throw TypeError('Authoring snapshots cannot contain cyclic data');
 if(Object.isFrozen(value))return value;
 visiting.add(value);
 for(const child of Object.values(value))freezeData(child,visiting);
 visiting.delete(value);
 return Object.freeze(value);
}

function snapshotCopy(snapshot:AuthoringSnapshot):AuthoringSnapshot {
 // Detach both imported JSON and a returned proposal from their caller's aliases.
 return freezeData(structuredClone(snapshot));
}

function sameData(left:unknown,right:unknown):boolean {
 if(Object.is(left,right))return true;
 if(left===null||right===null||typeof left!=='object'||typeof right!=='object'||
   Array.isArray(left)!==Array.isArray(right))return false;
 if(Array.isArray(left)&&Array.isArray(right)&&left.length!==right.length)return false;
 const keys=Object.keys(left),other=Object.keys(right);
 if(keys.length!==other.length)return false;
 return keys.every(key=>Object.prototype.hasOwnProperty.call(right,key)&&
   sameData((left as Record<string,unknown>)[key],(right as Record<string,unknown>)[key]));
}

function historyState(current:AuthoringSnapshot,past:readonly AuthoringSnapshot[],
 future:readonly AuthoringSnapshot[]):AuthoringHistory {
 return Object.freeze({current,past:Object.freeze(past),future:Object.freeze(future)});
}

export function createAuthoringHistory(snapshot:AuthoringSnapshot):AuthoringHistory {
 return historyState(snapshotCopy(snapshot),[],[]);
}

/**
 * Apply one complete immutable edit. Failed changes throw without modifying any
 * history data. Stale proposals are rejected before their change callback runs.
 * Equal JSON, including reordered object keys, does not consume undo or redo.
 */
export function commitAuthoring(history:AuthoringHistory,
 change:(current:AuthoringSnapshot)=>AuthoringSnapshot,expectedRevision?:object):AuthoringCommit {
 if(expectedRevision!==undefined&&expectedRevision!==history)
   return {history,disposition:'stale'};
 const proposed=change(history.current);
 if(proposed===history.current)return {history,disposition:'unchanged'};
 const current=snapshotCopy(proposed);
 if(sameData(history.current,current))return {history,disposition:'unchanged'};
 return {history:historyState(current,[...history.past,history.current].slice(-AUTHORING_HISTORY_LIMIT),[]),
   disposition:'committed'};
}

/** Stored snapshots retain identity, while every real navigation gets a new revision. */
export function undoAuthoring(history:AuthoringHistory):AuthoringHistory {
 if(!history.past.length)return history;
 return historyState(history.past[history.past.length-1],history.past.slice(0,-1),
   [history.current,...history.future]);
}

export function redoAuthoring(history:AuthoringHistory):AuthoringHistory {
 if(!history.future.length)return history;
 return historyState(history.future[0],[...history.past,history.current].slice(-AUTHORING_HISTORY_LIMIT),
   history.future.slice(1));
}
