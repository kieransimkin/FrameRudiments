/** Direct port gestures share exactly the same atomic graph checks as dropdowns. */
import type {Document} from './model.ts';
import {connectionIssue} from './graphEditor.ts';

export type GraphEndpoint={kind:'output';node:string}|{kind:'input';node:string;index:number};
export interface PendingConnection{snapshot:Document;revision:object;endpoint:GraphEndpoint;}
export interface GraphConnection{source:string;target:string;index:number;}
export interface PortSelection{pending:PendingConnection|null;connection:GraphConnection|null;error:string|null;}

export function selectGraphPort(project:Document,pending:PendingConnection|null,endpoint:GraphEndpoint,revision:object=project):PortSelection{
 const current=pending?.snapshot===project&&pending.revision===revision?pending:null;
 if(!current)return {pending:{snapshot:project,revision,endpoint},connection:null,error:null};
 const before=current.endpoint;
 if(before.kind===endpoint.kind){
   const same=before.node===endpoint.node&&(before.kind==='output'||endpoint.kind==='input'&&before.index===endpoint.index);
   return {pending:same?null:{snapshot:project,revision,endpoint},connection:null,error:null};
 }
 const source=before.kind==='output'?before.node:endpoint.node;
 const input=before.kind==='input'?before:endpoint as Extract<GraphEndpoint,{kind:'input'}>;
 const error=connectionIssue(project,input.node,input.index,source);
 if(error)return {pending:current,connection:null,error};
 return {pending:null,connection:{source,target:input.node,index:input.index},error:null};
}

export function pendingConnectionText(pending:PendingConnection|null):string{
 if(!pending)return 'Choose an output, then an input. Drag between ports or use the input selectors.';
 const endpoint=pending.endpoint;
 return endpoint.kind==='output'?`Connecting ${endpoint.node}: choose an input. Escape cancels.`:
   `Connecting ${endpoint.node} input ${endpoint.index+1}: choose an output. Escape cancels.`;
}
