import { AwardIcon, TrainFrontIcon } from "lucide-react";
import { Badge } from "#/components/ui/badge";
import {
	Card,
	CardContent,
	CardDescription,
	CardHeader,
	CardTitle,
} from "#/components/ui/card";
import type { ScoreProfile } from "#/lib/listing-filter-types";
import {
	SCORE_CATEGORY_LABELS,
	SCORE_PROFILE_LABELS,
	SCORE_PROFILE_WEIGHTS,
	type ScoreCategoryCode,
} from "#/lib/score-types";

const PROFILES: ScoreProfile[] = [
	"kamienica",
	"nowe_mieszkanie",
	"dom_airbnb",
	"dom_kolej",
	"dzialka_kolej",
	"dzialka_airbnb",
];

const CATEGORIES: Array<Exclude<ScoreCategoryCode, "penalty">> = [
	"price",
	"location",
	"attributes",
	"nature",
	"opportunity",
];

export default function ScoreMethodology() {
	return (
		<section
			id="jak-liczymy"
			aria-labelledby="score-method-heading"
			className="mt-16 scroll-mt-24"
		>
			<div className="flex flex-col gap-3">
				<Badge
					variant="outline"
					className="w-fit font-mono uppercase tracking-[0.16em]"
				>
					<AwardIcon />
					Metodologia
				</Badge>
				<h2
					id="score-method-heading"
					className="text-3xl font-semibold tracking-tight sm:text-4xl"
				>
					Jak liczymy punkty
				</h2>
				<p className="max-w-3xl text-sm leading-6 text-muted-foreground sm:text-base">
					Każdy profil ma osobny budżet 100 punktów. Cena, lokalizacja i
					atrybuty są skalowane do wag profilu, więc jeden sygnał nie może
					zdominować wyniku przypadkową liczbą punktów. Wynik pomaga wybrać
					oferty do sprawdzenia — nie zastępuje wyceny ani analizy prawnej.
				</p>
			</div>

			<Card className="mt-6 gap-0 overflow-hidden py-0">
				<CardHeader className="border-b px-4 py-4 sm:px-6">
					<CardTitle className="text-lg">Wagi profili</CardTitle>
					<CardDescription>
						Suma wag w każdym wierszu wynosi 100. Kary są odejmowane osobno.
					</CardDescription>
				</CardHeader>
				<CardContent className="overflow-x-auto px-0 py-0">
					<table className="w-full min-w-[720px] border-collapse text-left text-xs">
						<thead className="bg-muted/50 text-muted-foreground">
							<tr>
								<th scope="col" className="px-4 py-3 font-medium sm:px-6">
									Profil
								</th>
								{CATEGORIES.map((category) => (
									<th
										key={category}
										scope="col"
										className="px-3 py-3 text-right font-medium"
									>
										{SCORE_CATEGORY_LABELS[category]}
									</th>
								))}
							</tr>
						</thead>
						<tbody>
							{PROFILES.map((profile) => (
								<tr key={profile} className="border-t">
									<th scope="row" className="px-4 py-3 font-medium sm:px-6">
										{SCORE_PROFILE_LABELS[profile]}
									</th>
									{CATEGORIES.map((category) => (
										<td
											key={category}
											className="px-3 py-3 text-right font-mono text-muted-foreground"
										>
											{SCORE_PROFILE_WEIGHTS[profile][category]}
										</td>
									))}
								</tr>
							))}
						</tbody>
					</table>
				</CardContent>
			</Card>

			<div className="mt-4 grid gap-4 md:grid-cols-3">
				<Card>
					<CardHeader>
						<CardTitle className="text-base">Cena i wartość</CardTitle>
						<CardDescription className="leading-6">
							40% budżetu ceny pochodzi z lokalnych kwantyli, a 60% z porównania
							z podobnymi ofertami i transakcjami RCN. Gdy są dostępne oba
							źródła, używamy mniejszej zweryfikowanej przewagi cenowej. Pełnych
							punktów za porównanie nie przyznajemy bez wystarczającej liczby
							komparablów.
						</CardDescription>
					</CardHeader>
				</Card>
				<Card>
					<CardHeader>
						<CardTitle className="text-base">
							Dwa targety dla domów i działek
						</CardTitle>
						<CardDescription className="leading-6">
							Airbnb premiuje klimat, naturę i potencjał noclegowy. Kolej
							premiuje bliskość stacji, częstotliwość połączeń i funkcjonalność
							pod budowę albo zamieszkanie.
						</CardDescription>
					</CardHeader>
				</Card>
				<Card>
					<CardHeader>
						<CardTitle className="flex items-center gap-2 text-base">
							<TrainFrontIcon />
							Jawne wymagania
						</CardTitle>
						<CardDescription className="leading-6">
							Profil kolejowy wymaga stacji w promieniu 3 km z połączeniami do
							Krakowa, Katowic, Warszawy lub Wrocławia. Działka dodatkowo wymaga
							potwierdzonej możliwości zabudowy.
						</CardDescription>
					</CardHeader>
				</Card>
			</div>
		</section>
	);
}
