import type { LayerToggles } from "./constants";

const OPTIONS: Array<{ key: keyof LayerToggles; label: string; hint: string }> =
	[
		{ key: "rail", label: "Kolej", hint: "Linie kolejowe" },
		{
			key: "tram",
			label: "Tramwaje",
			hint: "Linie tramwajowe (od zbliżenia 10)",
		},
		{ key: "stops", label: "Przystanki", hint: "Stacje i przystanki" },
		{ key: "boundaries", label: "Granice", hint: "Województwa i powiaty" },
		{
			key: "traffic",
			label: "Ruch drogowy",
			hint: "Korki na głównych drogach (na żywo)",
		},
		{
			key: "heatmap",
			label: "Gęstość POI",
			hint: "Mapa cieplna punktów usługowych",
		},
		{
			key: "models3d",
			label: "Turbiny 3D",
			hint: "Modele glTF (demo: farma Margonin)",
		},
		{ key: "contours", label: "Warstwice", hint: "Poziomice z etykietami" },
		{ key: "hillshade", label: "Hillshade", hint: "Cieniowanie rzeźby" },
		{ key: "terrain", label: "Rzeźba 3D", hint: "Teren w skali 1:1" },
		{
			key: "objects3d",
			label: "Budynki 3D",
			hint: "Budynki, landmarki, drzewa (od zbliżenia 13)",
		},
		{ key: "elevation", label: "Wysokość", hint: "Odczyt n.p.m. pod kursorem" },
	];

export default function MapControls({
	toggles,
	onChange,
}: {
	toggles: LayerToggles;
	onChange: (key: keyof LayerToggles, value: boolean) => void;
}) {
	return (
		<div className="pointer-events-auto max-h-[calc(100vh-7rem)] w-64 overflow-y-auto rounded-xl border border-white/10 bg-slate-950/80 p-3 text-xs text-slate-200 shadow-xl backdrop-blur">
			<div className="mb-2 flex items-center justify-between">
				<span className="font-semibold text-slate-100">Warstwy</span>
				<span className="rounded-full border border-white/10 px-2 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">
					Noc
				</span>
			</div>
			<div className="space-y-1.5">
				{OPTIONS.map((option) => (
					<label
						key={option.key}
						className="flex cursor-pointer items-start gap-2 rounded-md px-1 py-0.5 hover:bg-white/5"
						title={option.hint}
					>
						<input
							type="checkbox"
							className="mt-0.5 size-3.5 accent-orange-400"
							checked={toggles[option.key]}
							onChange={(event) => onChange(option.key, event.target.checked)}
						/>
						<span className="leading-tight">
							<span className="block text-slate-100">{option.label}</span>
							<span className="block text-[10px] text-slate-400">
								{option.hint}
							</span>
						</span>
					</label>
				))}
			</div>
			<div className="mt-3 space-y-1 border-t border-white/10 pt-2 text-[10px] text-slate-300">
				<span className="flex items-center gap-1.5">
					<span className="inline-block h-0.5 w-5 rounded bg-orange-400" />
					kolej
				</span>
				<span className="flex items-center gap-1.5">
					<span className="inline-block h-0.5 w-5 rounded bg-sky-400" />
					tramwaje
				</span>
				<span className="flex items-center gap-1.5">
					<span className="inline-block size-2 rounded-full bg-amber-400" />
					stacje
				</span>
				<span className="flex items-center gap-1.5">
					<span className="inline-block size-2 rounded-full bg-sky-300" />
					przystanki tramwajowe
				</span>
				{toggles.traffic && (
					<>
						<span className="flex items-center gap-1.5">
							<span className="inline-block h-0.5 w-5 rounded bg-green-500" />
							płynnie
						</span>
						<span className="flex items-center gap-1.5">
							<span className="inline-block h-0.5 w-5 rounded bg-amber-500" />
							umiarkowanie
						</span>
						<span className="flex items-center gap-1.5">
							<span className="inline-block h-0.5 w-5 rounded bg-red-500" />
							korki
						</span>
					</>
				)}
			</div>
		</div>
	);
}
