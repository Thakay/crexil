import { describe, expect, it } from 'vitest';
import { ACTIONS, actionView, producedBy } from './actions';
import type { Material } from './api';
import { IMPORT_ACCEPT, MEDIA, mediumOf, sourceRoles } from './media';

describe('media registry', () => {
  it('gives every medium its own hue token, named after its noun', () => {
    for (const medium of Object.values(MEDIA)) expect(medium?.hue).toBe(`var(--medium-${medium?.noun})`);
  });
  it('imports every medium the registry knows', () => {
    expect(IMPORT_ACCEPT).toBe('image/png,image/jpeg,image/webp,image/tiff,.glb,model/gltf-binary');
  });
  it('shows a medium this client does not describe yet as a plain placeholder', () => {
    const unknown = mediumOf('video' as Material['payload']);
    expect(unknown).toMatchObject({noun: 'video', hasPreview: expect.any(Function), placeholder: 'Preview unavailable'});
    expect(unknown.hasPreview({} as Material)).toBe(false);
  });
  it('draws a mesh face only from a retained turntable', () => {
    const mesh = (has_turntable: boolean) => ({payload: '3d', mesh: {has_turntable}}) as unknown as Material;
    expect(mediumOf('3d').hasPreview(mesh(true))).toBe(true);
    expect(mediumOf('3d').hasPreview(mesh(false))).toBe(false);
    expect(mediumOf('image').hasPreview({payload: 'image'} as Material)).toBe(true);
  });
});

describe('preparation actions', () => {
  it('names the source roles a runner reads by position', () => {
    expect(sourceRoles('texture')).toEqual(['Shape · mesh', 'Appearance · image']);
    expect(sourceRoles('mesh')).toEqual(['Image']);
    expect(sourceRoles('decimate')).toEqual(['Shape · mesh']);
    expect(sourceRoles('generate')).toEqual([]);
    expect(sourceRoles(undefined)).toEqual([]);
  });
  it('knows what each action produces, and treats an unknown one as an image', () => {
    expect(producedBy('texture')).toBe('3d');
    expect(producedBy('combine')).toBe('image');
    expect(producedBy('not-an-action')).toBe('image');
    expect(actionView('toString')).toBeUndefined();
    expect(producedBy(undefined)).toBe('image');
    expect(actionView('mesh')?.recordedDescription).toBe(true);
  });
  it('offers the drawer the listed actions in order', () => {
    expect(Object.values(ACTIONS).filter(action => action?.listed).map(action => action?.label)).toEqual([
      'Generate from words', 'Continue from one source', 'Combine sources', 'Edit a region', 'Custom operation',
      'Generate 3D from image', 'Reduce mesh', 'Texture mesh from image', 'Crop, resize or arrange',
    ]);
  });
});
