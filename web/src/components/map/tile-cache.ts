/**
 * Mapbox GL's shared `mapbox-tiles` Cache API entry caches 404 tile bodies
 * and later hands a cached "Tile not found" JSON to the vector-tile parser,
 * which throws "Expected varint not more than 10 bytes". Live 404s render as
 * empty tiles; only the cached path is broken. Drop cached 404s after load
 * and interactions, and clear the whole tile cache if the parser error fires.
 */
const CACHE_NAME = "mapbox-tiles";
const MIN_PRUNE_INTERVAL_MS = 4_000;

/** Standard-style sources that legitimately 404 across most of the country. */
const SPARSE_TILE_SOURCES = [
	"hd-road-v1-bounded",
	"indoor-v3",
	"procedural-buildings-v1",
	"mapbox-landmark-pois-v1",
];

let lastPrune = 0;
let pruneInFlight = false;

/** Remove cached 404 responses, keeping valid tiles. Best-effort. */
export function pruneBadTileCache(force = false): void {
	if (typeof caches === "undefined") return;
	const now = Date.now();
	if (!force && now - lastPrune < MIN_PRUNE_INTERVAL_MS) return;
	if (pruneInFlight) return;
	lastPrune = now;
	pruneInFlight = true;
	void (async () => {
		try {
			const cache = await caches.open(CACHE_NAME);
			const requests = (await cache.keys()).filter((request) =>
				SPARSE_TILE_SOURCES.some((source) => request.url.includes(source)),
			);
			await Promise.all(
				requests.map(async (request) => {
					const response = await cache.match(request);
					if (response?.status === 404) await cache.delete(request);
				}),
			);
		} catch {
			// Cache API unavailable or evicted mid-scan: nothing to repair.
		} finally {
			pruneInFlight = false;
		}
	})();
}

/** Nuclear recovery for a cached-404 parse error: refetch every tile. */
export function resetTileCache(): void {
	if (typeof caches === "undefined") return;
	lastPrune = Date.now();
	void caches.delete(CACHE_NAME).catch(() => {
		// Cache API unavailable: the map still refetches from the network.
	});
}
