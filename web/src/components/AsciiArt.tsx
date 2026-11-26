import { type CSSProperties, useEffect, useRef } from "react";

type AsciiArtProps = {
	piece: string;
	size?: number;
	speed?: number;
	look?: string;
	mono?: boolean;
	unconstrained?: boolean;
	className?: string;
	style?: CSSProperties;
};

/**
 * Client-only mount for ASCII Magic's <ascii-art> custom element.
 *
 * The runtime is loaded by the home route's head(). Creating the element in an
 * effect keeps its shadow-DOM <pre> out of SSR hydration. The wrapper is
 * aria-hidden because every piece on the home page is decorative; the real
 * content and actions live next to it in normal, accessible markup.
 */
export default function AsciiArt({
	piece,
	size = 12,
	speed = 1,
	look,
	mono = false,
	unconstrained = false,
	className,
	style,
}: AsciiArtProps) {
	const hostRef = useRef<HTMLDivElement>(null);

	useEffect(() => {
		const host = hostRef.current;
		if (!host) return;

		const art = document.createElement("ascii-art");
		art.setAttribute("piece", piece);
		art.setAttribute("size", String(size));
		art.setAttribute("speed", String(speed));
		art.setAttribute("aria-hidden", "true");
		if (look) art.setAttribute("look", look);
		if (mono) art.setAttribute("mono", "");
		if (unconstrained) {
			art.style.maxWidth = "none";
			art.style.flexShrink = "0";
		}

		host.replaceChildren(art);
		return () => {
			art.remove();
		};
	}, [look, mono, piece, size, speed, unconstrained]);

	return <div ref={hostRef} aria-hidden className={className} style={style} />;
}
