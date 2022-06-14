#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import logging, json
from struct   import Struct
from argparse import ArgumentParser, FileType

import numpy
from ._image   import convertImage
from ._parsers import importImage
from ._util    import ArgParser

DEFAULT_OPTIONS = {
	"match":       ".*",
	"skipframes":  0,
	"crop":        ( 0, 0, 1e10, 1e10 ),
	"scale":       1.0,
	"bpp":         4,
	"palette":     "auto",
	"dither":      0.2,
	"scalemode":   "lanczos",
	"alpharange":  ( 0x20, 0xe0 ),
	"blackvalue":  0x0421,
	"padding":     0,
	"flipmode":    "preferUnflipped", # Unused
	# .TIM specific options
	"imagepos":    ( 0, 0 ),
	"palettepos":  ( 0, 0 )
}

TIM_HEADER_STRUCT  = Struct("< 2I")
TIM_HEADER_VERSION = 0x0010
TIM_SECTION_STRUCT = Struct("< I 4H")

GPU_DMA_BUFFER_SIZE = 128 # 32 words

## Main

def _createParser():
	parser = ArgParser("Converts an image into the .TIM format.")

	group = parser.add_argument_group("Configuration options")
	group.add_argument(
		"-s", "--set",
		action  = "append",
		type    = str,
		help    = "Set a conversion option (use JSON syntax to specify value)",
		metavar = "option=value"
	)

	group = parser.add_argument_group("File paths")
	group.add_argument(
		"inputFile",
		type = importImage,
		help = "path to JSON entry list"
	)
	group.add_argument(
		"outputFile",
		type = str,
		help = "Path to .TIM file to be generated; {name} and {frame} placeholders can be specified"
	)

	return parser

def main():
	parser = _createParser()
	args   = parser.parse_args()

	options = DEFAULT_OPTIONS.copy()

	for arg in (args.set or []):
		key, value = arg.split("=", 1)
		options[key.strip().lower()] = json.loads(value)

	x,  y  = options["imagepos"]
	px, py = options["palettepos"]

	images       = tuple(args.inputFile)
	name, frames = images[0]

	# Ensure only one image (or a spritesheet that contains only one image) was
	# passed. If the image has more than one frame, ensure that the {frame}
	# placeholder is present in the output path.
	if len(images) > 1:
		parser.error("more than one input image found, use '-s match=\"<regex>\"' to specify a regex that only matches a single image")

	if len(frames) > 1 and args.outputFile.find("{frame") == -1:
		parser.error("image has more than one frame but the output path doesn't contain a {frame} placeholder")

	for index, frame in enumerate(frames):
		path  = args.outputFile.format(name = name, frame = index)
		image = convertImage(frame, options)

		timData = bytearray(TIM_HEADER_STRUCT.pack(
			TIM_HEADER_VERSION,
			# Bit 3 signals the presence of a palette section in the file
			{ 4: 0x08, 8: 0x09, 16: 0x02 }[image.bpp]
		))

		# Generate the palette section.
		if image.bpp != 16:
			palette = image.palette.view(numpy.uint16)
			data    = palette.tobytes()

			timData.extend(TIM_SECTION_STRUCT.pack(
				TIM_SECTION_STRUCT.size + palette.size * 2,
				int(px),
				int(py),
				palette.size,
				1
			))
			timData.extend(data)

			if int(px) % 16:
				logging.warning("the palette's X offset is not aligned to 16 pixels")

		# Generate the image section.
		data = image.getPackedData().tobytes()

		timData.extend(TIM_SECTION_STRUCT.pack(
			TIM_SECTION_STRUCT.size + palette.size,
			int(x),
			int(y),
			*image.getPackedSize()
		))
		timData.extend(data)

		#if len(data) % GPU_DMA_BUFFER_SIZE:
			#logging.warning("packed image size is not a multiple of DMA buffer size, LoadImage() may hang!")

		with open(path, "wb") as _file:
			_file.write(timData)

if __name__ == "__main__":
	main()
