/**
 * Venue display order by approximate perpetual / derivatives volume rank.
 * Update periodically when market share shifts materially.
 */
export const CEX_VENUE_RANK = ["binance", "okx", "bybit", "bitget"] as const;

/** Perp-DEX by typical open-interest / volume tier (HL > dYdX > …). */
export const DEX_VENUE_RANK = [
  "hyperliquid",
  "dydx",
  "aster",
  "lighter",
  "edgex",
] as const;

export type CexVenueId = (typeof CEX_VENUE_RANK)[number];
export type DexVenueId = (typeof DEX_VENUE_RANK)[number];

export function sortByRank<T extends string>(
  ids: readonly T[],
  rank: readonly string[],
): T[] {
  const order = new Map(rank.map((id, i) => [id, i]));
  return [...ids].sort(
    (a, b) => (order.get(a) ?? 999) - (order.get(b) ?? 999),
  );
}

export type WalletSchemaFields = {
  fields?: unknown[];
  extra_fields?: unknown[];
};

export function credentialRowCount(schema?: WalletSchemaFields | null): number {
  if (!schema) return 0;
  return (schema.fields?.length ?? 0) + (schema.extra_fields?.length ?? 0);
}

/** Group venues by credential field count; within each group sort by volume rank. */
export function groupByCredentialRows(
  venueIds: readonly string[],
  schemas: Record<string, WalletSchemaFields> | undefined,
  rank: readonly string[],
): { rows: number; venues: string[] }[] {
  const rankMap = new Map(rank.map((id, i) => [id, i]));
  const byRows = new Map<number, string[]>();
  for (const id of venueIds) {
    const n = credentialRowCount(schemas?.[id]);
    const list = byRows.get(n) ?? [];
    list.push(id);
    byRows.set(n, list);
  }
  const minRank = (venues: string[]) =>
    Math.min(...venues.map((id) => rankMap.get(id) ?? 999));

  return [...byRows.entries()]
    .sort((a, b) => minRank(a[1]) - minRank(b[1]))
    .map(([rows, venues]) => ({
      rows,
      venues: [...venues].sort(
        (a, b) => (rankMap.get(a) ?? 999) - (rankMap.get(b) ?? 999),
      ),
    }));
}

export function venueRank(venueId: string, rank: readonly string[]): number {
  const i = rank.indexOf(venueId);
  return i >= 0 ? i + 1 : 0;
}
