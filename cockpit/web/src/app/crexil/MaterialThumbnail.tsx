// Faces load only retained previews. Material without one stays inspectable, and
// says so in its medium's words.
import { useState } from 'react';
import { imageUrl, label, type Material } from './api';
import { mediumOf } from './media';

export function MaterialThumbnail({node, decorative = false}: {node: Material; decorative?: boolean}) {
  const [failed, setFailed] = useState(false);
  const medium = mediumOf(node.payload);
  if (!node.available) return <span>Material unavailable</span>;
  if (medium.Plate) return <medium.Plate node={node} decorative={decorative}/>;
  if (failed || !medium.hasPreview(node)) return <span>{medium.placeholder}</span>;
  return <img src={imageUrl(node.id)} alt={decorative ? '' : label(node)} onError={() => setFailed(true)}/>;
}
