import { useState } from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { api } from '../api/client';
import type { ImageReferenceUpload, MediaReferenceInput } from '../api/types';
import { BriefConsole } from '../components/BriefConsole';
import { ImageReferenceInput } from '../components/ImageReferenceInput';

const owner = { kind: 'draft' as const, id: 'draft_frontend123' };
const binding = {
  reference_type: 'style' as const,
  binding_scope: 'project' as const,
  purpose: 'visual_style' as const,
};

function uploadResult(file: File, index = 1): ImageReferenceUpload {
  const artifactId = `upload_${index}`;
  return {
    artifact_id: artifactId,
    artifact_uri: `artifact://${artifactId}/v1`,
    version: 1,
    mime_type: file.type,
    size_bytes: file.size,
    width: 32,
    height: 18,
    created_at: new Date().toISOString(),
    sha256: 'a'.repeat(64),
    preview_url: `/artifacts/${artifactId}/preview`,
    thumbnail_url: `/artifacts/${artifactId}/thumbnail`,
    artifact: {} as ImageReferenceUpload['artifact'],
    reference: {
      reference_id: `reference_${index}`,
      reference_type: 'style',
      artifact_id: artifactId,
      version: 1,
      binding_scope: 'project',
      purpose: 'visual_style',
      selected: true,
      original_filename: file.name,
      mime_type: file.type as 'image/png',
      size_bytes: file.size,
      width: 32,
      height: 18,
    },
    binding,
  };
}

function Harness({ initial = [] }: { initial?: MediaReferenceInput[] }) {
  const [references, setReferences] = useState(initial);
  return (
    <ImageReferenceInput
      owner={owner}
      binding={binding}
      references={references}
      onReferencesChange={setReferences}
      label="Project visual references"
    />
  );
}

describe('image reference upload UX', () => {
  it('rejects multiple source files instead of silently discarding extras', async () => {
    const upload = vi.spyOn(api, 'uploadDraftReference');
    render(
      <ImageReferenceInput
        owner={owner}
        binding={{ ...binding, reference_type: 'source_image' }}
        references={[]}
        onReferencesChange={() => {}}
        multiple={false}
        label="Img2Img source"
      />,
    );
    fireEvent.change(screen.getByLabelText('Upload Img2Img source'), {
      target: {
        files: [
          new File(['one'], 'one.png', { type: 'image/png' }),
          new File(['two'], 'two.png', { type: 'image/png' }),
        ],
      },
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('Choose exactly one source image.');
    expect(upload).not.toHaveBeenCalled();
  });

  it('shows indeterminate upload status, backend preview, and unbind remove', async () => {
    let finish!: (value: ImageReferenceUpload) => void;
    const pending = new Promise<ImageReferenceUpload>((resolve) => {
      finish = resolve;
    });
    vi.spyOn(api, 'uploadDraftReference').mockReturnValue(pending);
    vi.spyOn(api, 'removeDraftReference').mockResolvedValue(undefined);
    render(<Harness />);
    const file = new File(['png-bytes'], 'pending.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText('Upload Project visual references'), {
      target: { files: [file] },
    });
    expect(await screen.findByText(/pending.png · Uploading/)).toBeVisible();
    finish(uploadResult(file));
    expect(await screen.findByText('pending.png')).toBeVisible();
    expect(screen.getByRole('img').getAttribute('src')).toContain(
      '/api/artifacts/upload_1/thumbnail?draft_id=draft_frontend123&version=1',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Remove pending.png' }));
    await waitFor(() =>
      expect(api.removeDraftReference).toHaveBeenCalledWith(owner.id, 'reference_1'),
    );
    await waitFor(() => expect(screen.queryByText('pending.png')).toBeNull());
  });

  it('supports multi-image drag/drop and rejects invalid files before transport', async () => {
    const upload = vi.spyOn(api, 'uploadDraftReference');
    upload.mockImplementation(async (_draft, file) => uploadResult(file, upload.mock.calls.length));
    const { container } = render(<Harness />);
    const first = new File(['one'], 'one.png', { type: 'image/png' });
    const second = new File(['two'], 'two.webp', { type: 'image/webp' });
    fireEvent.drop(container.querySelector('.reference-dropzone')!, {
      dataTransfer: { files: [first, second] },
    });
    await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('one.png')).toBeVisible();
    expect(await screen.findByText('two.webp')).toBeVisible();

    fireEvent.drop(container.querySelector('.reference-dropzone')!, {
      dataTransfer: { files: [new File(['bad'], 'bad.svg', { type: 'image/svg+xml' })] },
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('PNG, JPEG, or WebP');
    expect(upload).toHaveBeenCalledTimes(2);
  });

  it('restores a persisted reference using the backend artifact URL', () => {
    const file = new File(['persisted'], 'persisted.png', { type: 'image/png' });
    render(<Harness initial={[uploadResult(file).reference]} />);
    expect(screen.getByText('persisted.png')).toBeVisible();
    expect(screen.getByRole('img').getAttribute('src')).not.toContain('blob:');
  });

  it('places reusable upload controls only in visual creative settings', () => {
    render(
      <BriefConsole
        draft={{
          draft_id: owner.id,
          story_description: 'A visual story',
          creative_hints: {
            scene_seeds: [{ hint_id: 'hint_scene', title: 'Station', description: 'At dusk' }],
            key_visuals: [{ hint_id: 'hint_visual', description: 'A red moon' }],
            style_references: [
              { hint_id: 'hint_style', title: 'Arrival', kind: 'film', dimensions: ['visual'] },
            ],
          },
        }}
        onChange={() => {}}
        locked={false}
        busy={false}
        onStart={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Advanced' }));
    expect(screen.getByLabelText('Upload Project visual references')).toBeInTheDocument();
    expect(screen.getByLabelText('Upload Scene seed 1 images')).toBeInTheDocument();
    expect(screen.getByLabelText('Upload Key visual 1 images')).toBeInTheDocument();
    expect(screen.getByLabelText('Upload Style reference 1 images')).toBeInTheDocument();
    expect(screen.queryByLabelText(/Upload .*moment/i)).toBeNull();
    expect(screen.queryByLabelText(/Upload .*audio/i)).toBeNull();
    expect(screen.queryByLabelText(/Upload .*production/i)).toBeNull();
  });
});
