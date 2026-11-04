// Read-only binding of tested sources, shipped ASAR, bundled runtime and installer.
import asar from '@electron/asar';
import { readFile, readdir, writeFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
const [packageDir,output]=process.argv.slice(2);
if(!packageDir||!output)throw new Error('Usage: verify-knowledge-package.mjs win-unpacked output.json');
const root=process.cwd(),packed=path.resolve(packageDir),archive=path.join(packed,'resources','app.asar');
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
async function walk(folder){const files=[];for(const entry of await readdir(folder,{withFileTypes:true})){const p=path.join(folder,entry.name);if(entry.isDirectory())files.push(...await walk(p));else if(entry.isFile())files.push(p);}return files;}
const members=[];
for(const file of [...await walk(path.join(root,'dist')),...await walk(path.join(root,'electron'))]){
 const relative=path.relative(root,file),expected=await readFile(file),actual=asar.extractFile(archive,relative);
 if(digest(expected)!==digest(actual))throw new Error('ASAR source mismatch: '+relative);
 members.push({path:relative.replaceAll('\\','/'),sha256:digest(actual)});
}
const runtime=path.join(packed,'resources','video-runtime');
const manifest=JSON.parse(await readFile(path.join(runtime,'manifest.json'),'utf8'));
const runtimeFiles=manifest.files;
if(!Array.isArray(runtimeFiles))throw new Error('Runtime manifest missing files');
for(const row of runtimeFiles){
 const file=path.resolve(runtime,row.path);if(!file.startsWith(runtime+path.sep))throw new Error('Runtime manifest escape');
 if(digest(await readFile(file))!==row.sha256)throw new Error('Runtime mismatch: '+row.path);
}
const shipped=JSON.parse(asar.extractFile(archive,'package.json')),sourceMetadata=JSON.parse(await readFile(path.join(root,'package.json'),'utf8'));
// electron-builder removes these development-only fields from the app metadata.
for(const key of ['scripts','devDependencies','build'])delete sourceMetadata[key];
if(JSON.stringify(shipped)!==JSON.stringify(sourceMetadata))throw new Error('Packaged application metadata mismatch');
const version=shipped.version;
const result={passed:true,version,actual_asar:true,source_files:members.length,members,package_metadata_matches_after_builder_pruning:true,runtime_manifest_files:runtimeFiles.length,runtime_manifest_sha256:digest(await readFile(path.join(runtime,'manifest.json'))),asar_sha256:digest(await readFile(archive)),executable_sha256:digest(await readFile(path.join(packed,'迈医协同.exe'))),runtime_unchanged:true,nsis_executed:false};
await writeFile(output,JSON.stringify(result,null,2));console.log(JSON.stringify({passed:true,version,source_files:members.length,runtime_files:runtimeFiles.length}));
