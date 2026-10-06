// Setup reads configured adapters; credentials use the existing guarded local
// setup API. Opening this room never changes a connection or requests generation.
import {useEffect,useState} from 'react';
import {$api,fetchClient} from '../../api/client';
import {message,projectHref,result} from './api';
import s from './Filmstrip.module.css';

export function ConnectionsRoom() {
  const project=new URLSearchParams(location.hash.split('?')[1]).get('project')??'';
  const query=$api.useQuery('get','/api/crexil/setup');
  const [key,setKey]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [note,setNote]=useState('');
  const connections=query.data;
  const loadError:unknown=query.error;
  useEffect(()=>{
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'&&!busy){event.preventDefault();location.hash=projectHref(project||undefined);}};
    window.addEventListener('keydown',escape);return()=>window.removeEventListener('keydown',escape);
  },[project,busy]);
  const save=async(remove=false)=>{
    setBusy(true);setError('');setNote('');
    try {
      const status=remove?await result(fetchClient.DELETE('/api/setup/openrouter',{headers:{'X-Cockpit-Setup':'1'}})):
        await result(fetchClient.POST('/api/setup/openrouter',{headers:{'X-Cockpit-Setup':'1'},body:{api_key:key}}));
      setKey('');setNote(status.note??(remove?'Saved account access removed.':'Account access checked and saved.'));
      await query.refetch();
    }catch(error){setError(message(error));}finally{setBusy(false);}
  };
  const operations=`#/operations${project?`?project=${project}`:''}`;
  return <>
    <header className={s.topbar}><a className={s.back} href={projectHref(project||undefined)} aria-label="Return to project">‹</a><strong className={s.brand}>Connections</strong>
      <div className={s.spacer}/><a className={s.quiet} href={operations}>Prepared operations</a><button disabled={busy||query.isFetching} onClick={()=>void query.refetch()}>Check connections</button></header>
    <main className={s.connections}>
      <div className={s.pageHeading}><div><span className={s.eyebrow}>Start with what you have</span><h1>Keep working while setup is incomplete.</h1></div></div>
      <p>Import images or GLB files, prepare directions and inspect your record without connecting an engine. Generation begins only from a reviewed project request.</p>
      <a className={s.download} href={projectHref(project||undefined)}>{project?'Return to your project':'Start or open a project'}</a>
      {query.isLoading&&<p role="status">Checking configured connections…</p>}
      {Boolean(loadError)&&<p className={s.error} role="alert">{message(loadError)}<button onClick={()=>void query.refetch()}>Try again</button></p>}
      {connections?.preview&&<p className={s.connectionNotice}>This isolated preview keeps all real engines and account access disconnected. Import, preparation and the project record remain available.</p>}
      {connections&&<div className={s.connectionCards}>
        <section aria-label="Hosted account"><span className={s.eyebrow}>Hosted images</span><h2>OpenRouter</h2>
          <p>{connections.openrouter.configured?connections.openrouter.probe?.ok?'Account is reachable.':'Account is configured; access needs attention.':'No account access is configured.'}</p>
          {connections.openrouter.masked&&<p>Saved access · {connections.openrouter.masked}</p>}
          {connections.openrouter.probe?.error&&<p>{connections.openrouter.probe.error}</p>}
          <p>Use prepared generation or image-continuation operations. Their descriptions show source limits and pricing. Published tariffs reserve expected spending; they do not guarantee a final charge ceiling.</p>
          <form onSubmit={event=>{event.preventDefault();void save();}}>
            <label>OpenRouter API key<input type="password" autoComplete="off" spellCheck={false} value={key} disabled={busy||connections.preview} onChange={event=>setKey(event.target.value)}/></label>
            <div className={s.actions}><button disabled={busy||connections.preview||!key.trim()}>{busy?'Checking…':'Check and save account access'}</button>
              {connections.openrouter.configured&&<button type="button" disabled={busy||connections.preview} onClick={()=>void save(true)}>Remove saved key</button>}</div>
          </form>
          {connections.openrouter.env_overrides&&<p>The server's OPENROUTER_API_KEY setting takes precedence over a saved key. Change that setting and restart the server to change its active account.</p>}
          {error&&<p className={s.error} role="alert">{error}</p>}{note&&<p role="status">{note}</p>}
          <p className={s.hint}>The key stays on this installation. Checking access sends an account check, with no generation request.</p>
          <a href={operations}>Explore prepared operations ›</a>
        </section>
        <section aria-label="Local runtime"><span className={s.eyebrow}>Local images</span><h2>ComfyUI</h2>
          <p>{connections.comfyui.ok?`Runtime is reachable${connections.comfyui.version?` · ${connections.comfyui.version}`:''}.`:'The configured runtime is unavailable.'}</p>
          {!connections.preview&&<p className={s.hash}>{connections.comfyui_url}</p>}
          {connections.comfyui.error&&<p>{connections.comfyui.error}</p>}
          <p>Choose a prepared operation and check its required nodes, models and assets. Advertised requirements show what this runtime exposes; a recorded request establishes execution evidence.</p>
          <details><summary>Connect a compatible installation</summary>
            <ol><li>Start your ComfyUI installation with its API available to the Crexil server.</li>
              <li>Use the configured address, or set COMFYUI_URL in this installation's environment and restart Crexil. This room does not change or install a runtime.</li>
              <li>In Prepared operations, check the requirements. Resolve named missing nodes or assets with ComfyUI's own tools, then check again.</li>
              <li>Prepare a direction in a project, supply its sources and controls, then review the request before running.</li></ol>
            <p>Runtime reachability does not prove memory fit, model integrity or whether a node needs a first-use download. Keep those limits explicit in operation preparation.</p>
          </details>
          <a href={operations}>Choose and inspect an operation ›</a>
        </section>
      </div>}
      <section className={s.connectionCustom}><h2>Have your own workflow?</h2><p>Import its API export or supplied definition in Operations. The original stays intact while you map controls, sources and outputs. An incomplete registration can be saved and finished later.</p><a href={`${operations}${project?'&':'?'}import=1`}>Import a custom operation ›</a></section>
    </main>
  </>;
}
