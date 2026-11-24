import { ChevronDownIcon } from "lucide-react";
import { useState } from "react";
import { Badge } from "#/components/ui/badge";
import { Button } from "#/components/ui/button";
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
	Collapsible,
	CollapsibleContent,
	CollapsibleTrigger,
} from "#/components/ui/collapsible";
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
import { cn } from "#/lib/utils";
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
	const [open, setOpen] = useState(true);

	return (
		<Collapsible open={open} onOpenChange={setOpen}>
			<Card
				className={cn(
					"pointer-events-auto max-h-[calc(100vh-7rem)] overflow-hidden bg-card/95 backdrop-blur",
					open ? "w-72 gap-4 py-4" : "w-52 gap-0 py-2",
				)}
			>
				<CardHeader className={cn("shrink-0", !open && "px-3")}>
					<CardTitle>Warstwy</CardTitle>
					{open && (
						<CardDescription className="col-span-2">
							Wybierz warstwy mapy
						</CardDescription>
					)}
					<CardAction
						className={cn("flex items-center gap-1", open && "row-span-1")}
					>
						<Badge variant="secondary">Noc</Badge>
						<CollapsibleTrigger asChild>
							<Button
								variant="ghost"
								size="icon-sm"
								aria-label={open ? "Zwiń warstwy" : "Rozwiń warstwy"}
							>
								<ChevronDownIcon
									className={cn(
										"transition-transform duration-200",
										!open && "-rotate-90",
									)}
								/>
							</Button>
						</CollapsibleTrigger>
					</CardAction>
				</CardHeader>
				<CollapsibleContent className="flex min-h-0 flex-col gap-4">
					<CardContent className="min-h-0 overflow-y-auto">
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
					<CardFooter className="shrink-0 flex-col items-start gap-3">
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
				</CollapsibleContent>
			</Card>
		</Collapsible>
	);
}
