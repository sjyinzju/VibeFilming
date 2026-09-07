import { useRef, useState } from 'react';
import { ImagePlus, RefreshCw, Trash2, Upload } from 'lucide-react';
import { API_BASE, api } from '../api/client';
import type { MediaReferenceInput, ReferenceBindingInput } from '../api/types';
import { t } from '../i18n';

type Binding = ReferenceBindingInput;
type Reference = MediaReferenceInput;
type Owner = { kind: 'draft'; id: string } | { kind: 'project'; id: string };

const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);
const MAX_CLIENT_BYTES = 20 * 1024 * 1024;

function previewUrl(owner: Owner, reference: Reference, view: 'preview' | 'thumbnail') {
  const ownerQuery = owner.kind === 'draft' ? `draft_id=${owner.id}` : `project_id=${owner.id}`;
  return `${API_BASE}/artifacts/${encodeURIComponent(reference.artifact_id)}/${view}?${ownerQuery}&version=${reference.version || 1}`;
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 ** 2).toFixed(1)} MB`;
}

export function ImageReferenceInput({
  owner,
  binding,
  references,
  onReferencesChange,
  multiple = true,
  disabled = false,
  label = 'Image references',
}: {
  owner: Owner;
  binding: Binding;
  references: Reference[];
  onReferencesChange: (references: Reference[]) => void | Promise<void>;
  multiple?: boolean;
  disabled?: boolean;
  label?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [replaceReferenceId, setReplaceReferenceId] = useState<string>();
  const [broken, setBroken] = useState<Set<string>>(() => new Set());

  const remove = async (referenceId: string) => {
    setError('');
    try {
      if (owner.kind === 'draft') await api.removeDraftReference(owner.id, referenceId);
      else await api.removeProjectReference(owner.id, referenceId);
      await onReferencesChange(references.filter((item) => item.reference_id !== referenceId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not remove reference.');
    }
  };

  const uploadFiles = async (files: File[]) => {
    if (disabled || uploading.length > 0 || files.length === 0) return;
    if ((!multiple || replaceReferenceId) && files.length > 1) {
      setError('Choose exactly one source image.');
      return;
    }
    if (!multiple && references.length > 0 && !replaceReferenceId) {
      setError('Use Replace or Remove before adding another source image.');
      return;
    }
    const accepted = multiple && !replaceReferenceId ? files : files.slice(0, 1);
    const invalid = accepted.find(
      (file) => !IMAGE_TYPES.has(file.type) || file.size === 0 || file.size > MAX_CLIENT_BYTES,
    );
    if (invalid) {
      setError('Choose a non-empty PNG, JPEG, or WebP image up to 20 MB.');
      return;
    }
    setError('');
    setUploading(accepted.map((file) => file.name));
    let next = references;
    try {
      for (const file of accepted) {
        const result =
          owner.kind === 'draft'
            ? await api.uploadDraftReference(owner.id, file, binding)
            : await api.uploadProjectReference(owner.id, file, binding);
        if (replaceReferenceId) {
          if (owner.kind === 'draft') await api.removeDraftReference(owner.id, replaceReferenceId);
          else await api.removeProjectReference(owner.id, replaceReferenceId);
          next = next.filter((item) => item.reference_id !== replaceReferenceId);
        }
        next = [...next, result.reference];
      }
      await onReferencesChange(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Image upload failed.');
    } finally {
      setUploading([]);
      setReplaceReferenceId(undefined);
      if (input.current) input.current.value = '';
    }
  };

  return (
    <section className="reference-input" aria-label={t(label)}>
      <div
        className={`reference-dropzone ${disabled ? 'disabled' : ''}`}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          if (!disabled) void uploadFiles([...event.dataTransfer.files]);
        }}
      >
        <ImagePlus size={18} />
        <span>{t('Drop PNG, JPEG or WebP here')}</span>
        <button
          type="button"
          disabled={disabled || uploading.length > 0}
          onClick={() => input.current?.click()}
        >
          <Upload size={14} /> {t('Choose image')}
        </button>
        <input
          ref={input}
          className="visually-hidden"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          multiple={multiple && !replaceReferenceId}
          aria-label={t(`Upload ${label}`)}
          disabled={disabled}
          onChange={(event) => void uploadFiles([...(event.target.files || [])])}
        />
      </div>
      {uploading.map((name) => (
        <div className="reference-status" role="status" key={name}>
          <RefreshCw className="spin" size={14} /> {name} · {t('Uploading')}
        </div>
      ))}
      {error && <p role="alert">{t(error)}</p>}
      <div className="reference-gallery">
        {references.map((reference) => (
          <article
            className={`reference-card ${reference.selected ? 'selected' : ''}`}
            key={reference.reference_id}
          >
            {broken.has(reference.reference_id!) ? (
              <div className="broken-reference" role="status">
                {t('Broken reference')}
              </div>
            ) : (
              <a href={previewUrl(owner, reference, 'preview')} target="_blank" rel="noreferrer">
                <img
                  src={previewUrl(owner, reference, 'thumbnail')}
                  alt={reference.original_filename || 'Image reference'}
                  onError={() =>
                    setBroken((current) => new Set(current).add(reference.reference_id!))
                  }
                />
              </a>
            )}
            <div>
              <strong>{reference.original_filename || reference.artifact_id}</strong>
              <small>
                {reference.width || '?'}×{reference.height || '?'} ·{' '}
                {formatBytes(reference.size_bytes || 0)} · {t('Ready')}
              </small>
            </div>
            <div className="reference-actions">
              <button
                type="button"
                disabled={disabled}
                aria-label={t(`Replace ${reference.original_filename || 'reference'}`)}
                onClick={() => {
                  setReplaceReferenceId(reference.reference_id!);
                  input.current?.click();
                }}
              >
                <RefreshCw size={13} /> {t('Replace')}
              </button>
              <button
                type="button"
                disabled={disabled}
                aria-label={t(`Remove ${reference.original_filename || 'reference'}`)}
                onClick={() => void remove(reference.reference_id!)}
              >
                <Trash2 size={13} /> {t('Remove')}
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
