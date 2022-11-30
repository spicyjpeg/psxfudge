# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import logging
from pathlib import Path

from PIL       import Image
from ..image   import convertImage
from ..packer  import buildTexpages
from ..parsers import importImages
from ..util    import unpackNibbles2D, iteratePaths
from .common   import IMAGE_PROPERTIES, TIM_IMAGE_PROPERTIES, Tool, \
	MultiEntryTool

## Tool classes

class _FudgeTIM(Tool):
	def __init__(self):
		super().__init__(
			"Converts one or more images into the .TIM format.",
			IMAGE_PROPERTIES | TIM_IMAGE_PROPERTIES
		)

		self.addToolOptions()
		self.addConfigOptions()
		self.addFileOptions(( "name", "frame" ))

	def run(self, args, properties):
		x,  y  = properties["imagePos"]
		px, py = properties["palettePos"]

		images = tuple(importImages(
			iteratePaths(args.inputFile),
			args.properties
		))

		# Ensure the placeholders are present in the output path if there are
		# name or frame number conflicts.
		if len(images) > 1 and ("{name" not in args.outputFile):
			self.parser.error("more than one image to convert but the output path doesn't contain a {name} placeholder")

		for name, frames in images:
			if len(frames) > 1 and ("{frame" not in args.outputFile):
				self.parser.error(f"image '{name}' has more than one frame but the output path doesn't contain a {{frame}} placeholder")

		for name, frames in images:
			logging.info(f"processing {name} (frames: {len(frames)})")

			for index, frame in enumerate(frames):
				image = convertImage(frame, args.properties)
				path  = Path(str(args.outputFile).format(
					name  = name,
					frame = index
				))

				image.x,  image.y  = int(x),  int(y)
				image.px, image.py = int(px), int(py)

				with path.open("wb") as _file:
					_file.write(image.toTIM())

				logging.info(f"saved {path.name}")

## Exports

fudgetim = _FudgeTIM()
