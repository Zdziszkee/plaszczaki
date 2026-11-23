import type { ErrorComponentProps } from "@tanstack/react-router";

export default function ErrorComponent({ error, reset }: ErrorComponentProps) {
	const detail =
		import.meta.env.DEV && error instanceof Error
			? error.message
			: "Something went wrong while loading this page. Please try again.";

	return (
		<main className="mx-auto flex min-h-[60vh] w-full max-w-3xl flex-col items-center justify-center px-4 py-20 text-center">
			<p className="mb-3 text-sm font-semibold tracking-[0.24em] text-[var(--lagoon-deep)] uppercase">
				Error
			</p>
			<h1 className="mb-4 text-4xl font-bold tracking-tight text-[var(--sea-ink)] sm:text-5xl">
				Something went wrong
			</h1>
			<p className="m-0 max-w-md text-base leading-8 text-[var(--sea-ink-soft)]">
				{detail}
			</p>
			<button
				type="button"
				onClick={reset}
				className="mt-8 cursor-pointer rounded-full border border-[rgba(50,143,151,0.3)] bg-[rgba(79,184,178,0.14)] px-5 py-2.5 text-sm font-semibold text-[var(--lagoon-deep)] transition hover:-translate-y-0.5 hover:bg-[rgba(79,184,178,0.24)]"
			>
				Try again
			</button>
		</main>
	);
}
