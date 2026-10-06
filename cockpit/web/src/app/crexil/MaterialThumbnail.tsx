// Faces load only retained thumbnails. A mesh with no poster stays inspectable.
import { useState } from 'react';
import { imageUrl, label, type Material } from './api';

export function MaterialThumbnail({node, decorative=false}: {node:Material; decorative?:boolean}) {
  const [failed,setFailed]=useState(false);
  if(!node.available)return <span>Material unavailable</span>;
  if(failed||node.payload==='3d'&&!node.mesh?.has_turntable)return <span>{node.payload==='3d'?'3D material':'Image preview unavailable'}</span>;
  return <img src={imageUrl(node.id)} alt={decorative?'':label(node)} onError={()=>setFailed(true)}/>;
}
