#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import logging
from pathlib import Path

from ._parsers  import importImages
from ._image    import convertImage
from ._builders import generateTIM
from ._util     import normalizePaths, ArgParser

DEFAULT_PROPERTIES = {
	"match":       ".*",
	"frames":      "0-255",
	"crop":        ( 0, 0, 0x10000, 0x10000 ),
	"scale":       1.0,
	"bpp":         4,
	"palette":     "auto",
	"dither":      0.5,
	"scaleMode":   "lanczos",
	"alphaRange":  ( 0x20, 0xe0 ),
	"blackValue":  ( 1, 1, 1, 0 ),
	"cropMode":    "none",
	"padding":     0,
	"flipMode":    "preferUnflipped", # Unused
	# .TIM specific options
	"imagePos":    ( 0, 0 ),
	"palettePos":  ( 0, 0 )
}

## Main

def _createParser():
	parser = ArgParser("Converts one or more images into the .TIM format.", DEFAULT_PROPERTIES)

	group = parser.add_argument_group("File paths")
	group.add_argument(
		"inputFile",
		type  = str,
		nargs = "+",
		help  = "Paths to input images"
	)
	group.add_argument(
		"outputFile",
		type = str,
		help = "Path to file(s) to be generated; {name} and {frame} placeholders can be specified"
	)

	return parser

def main():
	parser = _createParser()
	args   = parser.parse()

	x,  y  = args.properties["imagePos"]
	px, py = args.properties["palettePos"]

	images = tuple(importImages(
		normalizePaths(args.inputFile),
		args.properties
	))

	# Ensure the placeholders are present in the output path if there are name
	# or frame number conflicts.
	if len(images) > 1 and ("{name" not in args.outputFile):
		parser.error("more than one image to convert but the output path doesn't contain a {name} placeholder")

	for name, frames in images:
		if len(frames) > 1 and ("{frame" not in args.outputFile):
			parser.error(f"image '{name}' has more than one frame but the output path doesn't contain a {{frame}} placeholder")

	for name, frames in images:
		logging.info(f"processing {name} (frames: {len(frames)})")

		for index, frame in enumerate(frames):
			image = convertImage(frame, args.properties)
			path  = Path(args.outputFile.format(
				name  = name,
				frame = index
			))

			image.x,  image.y  = int(x),  int(y)
			image.px, image.py = int(px), int(py)

			with path.open("wb") as _file:
				_file.write(generateTIM(image))

			logging.info(f"saved {path.name}")

if __name__ == "__main__":
	main()
