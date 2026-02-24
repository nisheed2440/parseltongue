import yaml from "js-yaml";

/**
 * @typedef {Object} StoryMeta
 * @property {string} story_id
 * @property {string} title
 * @property {string} author
 * @property {string} [summary]
 * @property {string} [url]
 * @property {string[]} [tags]
 * @property {number} [word_count]
 * @property {number} [chapter_count]
 * @property {string} [language]
 */

/**
 * @param {StoryMeta} meta
 * @returns {string} YAML string for meta.yaml
 */
export function toYaml(meta) {
  const obj = {
    story_id: meta.story_id,
    title: meta.title,
    author: meta.author,
    summary: meta.summary ?? "",
    url: meta.url ?? "",
    tags: meta.tags ?? [],
    word_count: meta.word_count ?? 0,
    chapter_count: meta.chapter_count ?? 0,
    language: meta.language ?? "en",
  };
  return yaml.dump(obj, { lineWidth: -1, noRefs: true });
}
