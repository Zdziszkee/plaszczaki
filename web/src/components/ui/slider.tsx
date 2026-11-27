import { Slider as SliderPrimitive } from "radix-ui";
import type * as React from "react";
import { cn } from "#/lib/utils";

function Slider({
	className,
	id,
	...props
}: React.ComponentProps<typeof SliderPrimitive.Root>) {
	return (
		<SliderPrimitive.Root
			id={id}
			data-slot="slider"
			className={cn(
				"relative flex w-full touch-none items-center select-none data-[disabled]:opacity-50",
				className,
			)}
			{...props}
		>
			<SliderPrimitive.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-muted">
				<SliderPrimitive.Range className="absolute h-full bg-primary" />
			</SliderPrimitive.Track>
			{props.value?.map((_, index) => (
				<SliderPrimitive.Thumb
					// biome-ignore lint/suspicious/noArrayIndexKey: Radix thumbs are positional and must keep stable identity while dragging.
					key={`thumb-${index}`}
					id={props.value?.length === 1 ? id : undefined}
					aria-label={props["aria-label"]}
					data-slot="slider-thumb"
					className="block size-4 rounded-full border border-primary/50 bg-background shadow-sm transition-colors outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50"
				/>
			))}
		</SliderPrimitive.Root>
	);
}

export { Slider };
