import { createFileRoute } from "@tanstack/react-router";
import { eq } from "drizzle-orm";
import { db } from "#/db";
import { listing, source } from "#/db/schema";
import type { ListingDetails } from "#/lib/listing-details";

const DESCRIPTION_PREVIEW_LENGTH = 600;

function parseListingId(value: string): number | null {
	const id = Number(value);
	return Number.isSafeInteger(id) && id > 0 ? id : null;
}

function descriptionPreview(value: string | null): string | null {
	const normalized = value?.replace(/\s+/g, " ").trim();
	if (!normalized) return null;
	if (normalized.length <= DESCRIPTION_PREVIEW_LENGTH) return normalized;

	const truncated = normalized.slice(0, DESCRIPTION_PREVIEW_LENGTH);
	const lastSpace = truncated.lastIndexOf(" ");
	const end =
		lastSpace > DESCRIPTION_PREVIEW_LENGTH * 0.7 ? lastSpace : truncated.length;
	return `${truncated.slice(0, end).trimEnd()}…`;
}

export const Route = createFileRoute("/api/listings/$id")({
	server: {
		handlers: {
			GET: async ({ params }) => {
				const id = parseListingId(params.id);
				if (id === null) {
					return Response.json(
						{ error: "Nieprawidłowy identyfikator oferty." },
						{ status: 400 },
					);
				}

				const [row] = await db
					.select({
						id: listing.id,
						sourceId: listing.sourceId,
						sourceLabel: source.label,
						externalId: listing.externalId,
						url: listing.url,
						title: listing.title,
						description: listing.description,
						offerType: listing.offerType,
						propertyType: listing.propertyType,
						market: listing.market,
						priceKind: listing.priceKind,
						priceUnit: listing.priceUnit,
						pricePln: listing.pricePln,
						rentPln: listing.rentPln,
						depositPln: listing.depositPln,
						pricePerM2Pln: listing.pricePerM2Pln,
						areaM2: listing.areaM2,
						landAreaM2: listing.landAreaM2,
						rooms: listing.rooms,
						floor: listing.floor,
						floorLabel: listing.floorLabel,
						buildYear: listing.buildYear,
						condition: listing.condition,
						buildingType: listing.buildingType,
						buildingMaterial: listing.buildingMaterial,
						heating: listing.heating,
						windowsType: listing.windowsType,
						ownership: listing.ownership,
						energyCertificate: listing.energyCertificate,
						ceilingHeightM: listing.ceilingHeightM,
						buildingFloors: listing.buildingFloors,
						elevator: listing.elevator,
						parking: listing.parking,
						parkingSpaces: listing.parkingSpaces,
						balcony: listing.balcony,
						terrace: listing.terrace,
						garden: listing.garden,
						furnished: listing.furnished,
						petsAllowed: listing.petsAllowed,
						airConditioning: listing.airConditioning,
						security: listing.security,
						internet: listing.internet,
						fenced: listing.fenced,
						administrativeRentPln: listing.administrativeRentPln,
						estimatePln: listing.estimatePln,
						availableFrom: listing.availableFrom,
						voivodeship: listing.voivodeship,
						county: listing.county,
						commune: listing.commune,
						city: listing.city,
						district: listing.district,
						street: listing.street,
						addressText: listing.addressText,
						images: listing.images,
						sellerType: listing.sellerType,
						sellerName: listing.sellerName,
						publishedAt: listing.publishedAt,
						firstSeenAt: listing.firstSeenAt,
						auctionDate: listing.auctionDate,
						auctionRound: listing.auctionRound,
						caseNumber: listing.caseNumber,
						score: listing.score,
						scoreProfile: listing.scoreProfile,
						scores: listing.scores,
						scoreReasons: listing.scoreReasons,
						scoreBreakdown: listing.scoreBreakdown,
						scoreVersion: listing.scoreVersion,
					})
					.from(listing)
					.innerJoin(source, eq(source.id, listing.sourceId))
					.where(eq(listing.id, id))
					.limit(1);

				if (!row) {
					return Response.json(
						{ error: "Nie znaleziono oferty." },
						{ status: 404 },
					);
				}

				const details: ListingDetails = {
					id: row.id,
					sourceId: row.sourceId,
					sourceLabel: row.sourceLabel,
					externalId: row.externalId,
					url: row.url,
					title: row.title,
					descriptionPreview: descriptionPreview(row.description),
					offerType: row.offerType,
					propertyType: row.propertyType,
					market: row.market,
					priceKind: row.priceKind,
					priceUnit: row.priceUnit,
					pricePln: row.pricePln,
					rentPln: row.rentPln,
					depositPln: row.depositPln,
					pricePerM2Pln: row.pricePerM2Pln,
					areaM2: row.areaM2,
					landAreaM2: row.landAreaM2,
					rooms: row.rooms,
					floor: row.floor,
					floorLabel: row.floorLabel,
					buildYear: row.buildYear,
					condition: row.condition,
					buildingType: row.buildingType,
					buildingMaterial: row.buildingMaterial,
					heating: row.heating,
					windowsType: row.windowsType,
					ownership: row.ownership,
					energyCertificate: row.energyCertificate,
					ceilingHeightM: row.ceilingHeightM,
					buildingFloors: row.buildingFloors,
					elevator: row.elevator,
					parking: row.parking,
					parkingSpaces: row.parkingSpaces,
					balcony: row.balcony,
					terrace: row.terrace,
					garden: row.garden,
					furnished: row.furnished,
					petsAllowed: row.petsAllowed,
					airConditioning: row.airConditioning,
					security: row.security,
					internet: row.internet,
					fenced: row.fenced,
					administrativeRentPln: row.administrativeRentPln,
					estimatePln: row.estimatePln,
					availableFrom: row.availableFrom?.toISOString() ?? null,
					voivodeship: row.voivodeship,
					county: row.county,
					commune: row.commune,
					city: row.city,
					district: row.district,
					street: row.street,
					addressText: row.addressText,
					images: row.images,
					sellerType: row.sellerType,
					sellerName: row.sellerName,
					publishedAt: row.publishedAt?.toISOString() ?? null,
					firstSeenAt: row.firstSeenAt.toISOString(),
					auctionDate: row.auctionDate?.toISOString() ?? null,
					auctionRound: row.auctionRound,
					caseNumber: row.caseNumber,
					score: row.score,
					scoreProfile: row.scoreProfile,
					scores: row.scores,
					scoreReasons: row.scoreReasons,
					scoreBreakdown: row.scoreBreakdown,
					scoreVersion: row.scoreVersion,
				};

				return Response.json(details, {
					headers: { "Cache-Control": "public, max-age=60" },
				});
			},
		},
	},
});
