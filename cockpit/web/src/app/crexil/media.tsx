// The client's media registry (law 20): each payload names its noun, lineage hue,
// importable file types, face plate and inspection body. Surfaces read this table
// and never branch on a payload, so a new medium is one entry here plus its hue token.
import type { ComponentType } from 'react';
import { actionView, type Action, type Payload } from './actions';
import { imageUrl, label, type Material } from './api';
import { MeshInspection } from './MeshInspection';

export type MediumView = {
  /** The medium's name inside roles and notes: "image", "mesh". */
  noun: string;
  /** The lineage stroke for a connection whose child is this medium. */
  hue: string;
  /** File types the import picker offers for this medium. */
  accept: string[];
  /** Whether a retained preview can be drawn on a face. */
  hasPreview: (node: Material) => boolean;
  /** What a face says when there is no preview to draw. */
  placeholder: string;
  /** A plate of its own in place of the retained preview, for a medium that is not drawn as a picture. */
  Plate?: ComponentType<{node: Material; decorative?: boolean}>;
  /** The inspection body and its frame. */
  Inspect: ComponentType<{node: Material}>;
  inspectFrame: 'fullImage' | 'fullMesh';
  /** Its instructions are a description kept for the record, never sent. */
  recordedDescription: boolean;
};

export const MEDIA: {readonly [K in Payload]?: MediumView} = {
  image: {
    noun: 'image', hue: 'var(--medium-image)',
    accept: ['image/png', 'image/jpeg', 'image/webp', 'image/tiff'],
    hasPreview: () => true, placeholder: 'Image preview unavailable',
    Inspect: ({node}) => <img src={imageUrl(node.id, true)} alt={label(node)}/>, inspectFrame: 'fullImage',
    recordedDescription: false,
  },
  '3d': {
    noun: 'mesh', hue: 'var(--medium-mesh)',
    accept: ['.glb', 'model/gltf-binary'],
    hasPreview: node => Boolean(node.mesh?.has_turntable), placeholder: '3D material',
    Inspect: ({node}) => <MeshInspection key={node.id} node={node}/>, inspectFrame: 'fullMesh',
    recordedDescription: true,
  },
};

function NoInspector() {
  return <p>This medium has no inspection view yet. Its record remains.</p>;
}

/** A medium's view. One this client does not describe yet still shows, as a plain placeholder. */
export function mediumOf(payload: Payload): MediumView {
  return MEDIA[payload] ?? {noun: String(payload), hue: 'var(--medium-image)', accept: [], hasPreview: () => false,
    placeholder: 'Preview unavailable', Inspect: NoInspector, inspectFrame: 'fullImage', recordedDescription: false};
}

/** The import picker's accepted file types, every medium together. */
export const IMPORT_ACCEPT = Object.values(MEDIA).flatMap(medium => medium?.accept ?? []).join(',');

/** The ordered source roles an action reads, in words: "Shape · mesh", "Image". */
export function sourceRoles(action: Action | null | undefined): string[] {
  return (actionView(action)?.sources ?? []).map(({medium, role}) => {
    const noun = mediumOf(medium).noun;
    return role ? `${role} · ${noun}` : noun[0].toUpperCase() + noun.slice(1);
  });
}
