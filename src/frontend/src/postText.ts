import { t } from './i18n';

/** True for posts created by an analytics import (they were never written in Heardloop). */
export const isImported = (post: { external_source?: string | null }): boolean => !!post.external_source;

/**
 * The text to show for a post body. An imported post can have no text yet; the UI then shows a placeholder
 * (`placeholder: true`, so the caller can render it muted). `placeholderText` defaults to the long hint.
 */
export function displaySnippet(
  body: string,
  imported: boolean,
  placeholderText: string = t.posts.importedPlaceholder,
): { text: string; placeholder: boolean } {
  if (imported && !body.trim()) return { text: placeholderText, placeholder: true };
  return { text: body, placeholder: false };
}
