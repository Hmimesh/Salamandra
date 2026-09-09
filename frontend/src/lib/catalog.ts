export function normalizeSearch(value: string) {
  return value.normalize("NFKC").toLowerCase().replaceAll("ß", "ss").replace(/[\p{P}\p{Z}\s]+/gu, " ").trim();
}
