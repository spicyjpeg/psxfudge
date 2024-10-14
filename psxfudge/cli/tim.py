# -*- coding: utf-8 -*-
# (C) 2022-2023 spicyjpeg

import logging
from pathlib import Path

from ..image   import convertImage
from ..parsers import importImages
from ..util    import iteratePaths
from .common   import IMAGE_PROPERTIES, TIM_IMAGE_PROPERTIES, Tool

## Tool classes

class _FudgeTIM(Tool):
	def __init__(self):
		super().__init__(
			"fudgetim",
			"Converts one or more images into the .TIM format.",
			IMAGE_PROPERTIES | TIM_IMAGE_PROPERTIES
		)

		self.addToolOptions()
		self.addConfigOptions()
		self.addFileOptions(( "name", "frame" ))

	def run(self, args, properties):
		x,  y  = properties["imagePos"]
		px, py = properties["palettePos"]

		outputPath = str(args.outputFile)
		images     = tuple(importImages(
			iteratePaths(args.inputFile), properties
		))

		# Ensure the placeholders are present in the output path if there are
		# name, frame or mipmap level number conflicts.
		if len(images) > 1 and ("{name" not in outputPath):
			self.parser.error("more than one image to convert but the output path doesn't contain a {name} placeholder")

		for name, frames in images:
			if len(frames) > 1 and ("{frame" not in outputPath):
				self.parser.error(f"image '{name}' has more than one frame but the output path doesn't contain a {{frame}} placeholder")

			logging.info(f"processing {name} (frames: {len(frames)})")

			for index, frame in enumerate(frames):
				mipLevels = tuple(convertImage(frame, properties))

				if len(mipLevels) > 1 and ("{mip" not in outputPath):
					self.parser.error(f"image '{name}' has more than one mipmap level but the output path doesn't contain a {{mip}} placeholder")

				for mip, image in enumerate(mipLevels):
					path  = Path(outputPath.format(
						name  = name,
						frame = index,
						mip   = mip
					))

					image.x,  image.y  = int(x),  int(y)
					image.px, image.py = int(px), int(py)

					with path.open("wb") as _file:
						_file.write(image.toTIM())

				logging.info(f"saved {path.name}")

## Exports

fudgetim = _FudgeTIM()

if __name__ == "__main__":
	fudgetim()
