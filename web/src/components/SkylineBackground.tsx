import AsciiArt from "#/components/AsciiArt";

/**
 * Full-viewport city atmosphere for the home page.
 *
 * ASCII Magic's nyc-skyline-night is 104 x 36. Its monospace cells are 0.6em
 * wide and 1em tall, so max(100vw / 62.4, 100vh / 36) covers the viewport.
 * The CSS override lives in styles.css because the runtime sets --ascii-size
 * inline from its size attribute. Monochrome + low opacity keeps the shadcn
 * interface in front; the gradient leaves the copy areas quiet.
 */
export default function SkylineBackground() {
	return (
		<div
			aria-hidden
			className="pointer-events-none fixed inset-0 -z-10 overflow-hidden"
		>
			<AsciiArt
				piece="nyc-skyline-night"
				size={12}
				speed={0.65}
				mono
				unconstrained
				className="ascii-skyline absolute inset-0 flex items-center justify-center opacity-[0.1]"
			/>
			<div className="ascii-backdrop-fade absolute inset-0" />
		</div>
	);
}
