import { Badge } from "#/components/ui/badge";
import {
	Card,
	CardAction,
	CardContent,
	CardDescription,
	CardFooter,
	CardHeader,
	CardTitle,
} from "#/components/ui/card";
import { Checkbox } from "#/components/ui/checkbox";
import {
	Field,
	FieldContent,
	FieldDescription,
	FieldGroup,
	FieldLabel,
	FieldLegend,
	FieldSet,
} from "#/components/ui/field";
import { Separator } from "#/components/ui/separator";
import type { LayerToggles } from "./constants";

const OPTIONS: Array<{ key: keyof LayerToggles; label: string; hint: string }> =
	[
		{ key: "rail", label: "Kolej", hint: "Linie kolejowe" },
		{ key: "tram", label: "Tramwaje", hint: "Od zbliżenia 10" },
		{ key: "stops", label: "Przystanki", hint: "Stacje i przystanki" },
		{ key: "boundaries", label: "Granice", hint: "Województwa i powiaty" },
		{ key: "traffic", label: "Ruch drogowy", hint: "Korki (na żywo)" },
		{ key: "heatmap", label: "Gęstość POI", hint: "Mapa cieplna POI" },
		{ key: "models3d", label: "Turbiny 3D", hint: "glTF (demo: Margonin)" },
		{ key: "contours", label: "Warstwice", hint: "Poziomice z etykietami" },
		{ key: "hillshade", label: "Hillshade", hint: "Cieniowanie rzeźby" },
		{ key: "terrain", label: "Rzeźba 3D", hint: "Teren w skali 1:1" },
		{ key: "objects3d", label: "Budynki 3D", hint: "Od zbliżenia 13" },
		{ key: "elevation", label: "Wysokość", hint: "Odczyt n.p.m." },
	];

export default function MapControls({
	toggles,
	onChange,
}: {
	toggles: LayerToggles;
	onChange: (key: keyof LayerToggles, value: boolean) => void;
}) {
	return (
		<Card className="pointer-events-auto max-h-[calc(100vh-7rem)] w-72 gap-4 overflow-y-auto bg-card/95 py-4 backdrop-blur">
			<CardHeader>
				<CardTitle>Warstwy</CardTitle>
				<CardDescription>Wybierz warstwy mapy</CardDescription>
				<CardAction>
					<Badge variant="secondary">Noc</Badge>
				</CardAction>
			</CardHeader>
			<CardContent>
				<FieldSet>
					<FieldLegend variant="label" className="sr-only">
						Warstwy mapy
					</FieldLegend>
					<FieldGroup className="gap-1.5">
						{OPTIONS.map((option) => (
							<Field
								key={option.key}
								orientation="horizontal"
								className="items-start gap-3"
							>
								<Checkbox
									id={`layer-${option.key}`}
									checked={toggles[option.key]}
									onCheckedChange={(checked) =>
										onChange(option.key, checked === true)
									}
								/>
								<FieldContent>
									<FieldLabel
										htmlFor={`layer-${option.key}`}
										className="text-sm font-normal"
									>
										{option.label}
									</FieldLabel>
									<FieldDescription className="text-xs">
										{option.hint}
									</FieldDescription>
								</FieldContent>
							</Field>
						))}
					</FieldGroup>
				</FieldSet>
			</CardContent>
			<CardFooter className="flex-col items-start gap-3">
				<Separator />
				<span className="text-xs font-medium text-muted-foreground">
					Legenda
				</span>
				<div className="grid grid-cols-2 gap-1.5">
					<Badge variant="outline">
						<span className="size-1.5 rounded-full bg-orange-400" />
						kolej
					</Badge>
					<Badge variant="outline">
						<span className="size-1.5 rounded-full bg-sky-400" />
						tramwaje
					</Badge>
					<Badge variant="outline">
						<span className="size-1.5 rounded-full bg-amber-400" />
						stacje
					</Badge>
					<Badge variant="outline">
						<span className="size-1.5 rounded-full bg-sky-300" />
						przystanki
					</Badge>
					{toggles.traffic && (
						<>
							<Badge variant="outline">
								<span className="size-1.5 rounded-full bg-green-500" />
								płynnie
							</Badge>
							<Badge variant="outline">
								<span className="size-1.5 rounded-full bg-amber-500" />
								umiarkowanie
							</Badge>
							<Badge variant="outline">
								<span className="size-1.5 rounded-full bg-red-500" />
								korki
							</Badge>
						</>
					)}
				</div>
			</CardFooter>
		</Card>
	);
}
