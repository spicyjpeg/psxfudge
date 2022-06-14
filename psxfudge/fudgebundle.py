#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import os, re, logging, json
from struct   import Struct
from argparse import ArgumentParser, FileType

import numpy, av
from PIL       import Image
from ._file    import BundleBuilder
from ._image   import convertImage
from ._parsers import importImage
from ._avenc   import convertSound
from ._util    import unpackNibbles2D, globPaths, parseJSON, ArgParser

DEFAULT_OPTIONS = {
	# Image options
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
	"flipmode":    "preferUnflipped",
	# Sound options
	"chunklength": 0x6800,
	"samplerate":  44100,
	"channels":    1,
	"loopstart":   -1.0,
	"loopend":     -1.0
}

## Main

def _createParser():
	parser = ArgParser("Converts source assets listed in a JSON file and builds an asset bundle.")

	group = parser.add_argument_group("Texture packing options")
	group.add_argument(
		"-D", "--discard-step",
		type    = int,
		default = 1,
		help    = "How tightly to pack texture atlases (default is 1); increase to improve packing speed",
		metavar = "1-128"
	)
	group.add_argument(
		"-T", "--try-splits",
		action = "store_true",
		help   = "Attempt to use multiple criteria when subdividing free space (may worsen packing ratio!)"
	)
	group.add_argument(
		"-P", "--preserve-palettes",
		action = "store_true",
		help   = "Disable deduplicating 'similar' palettes (only deduplicate palettes that are actually identical)"
	)
	group.add_argument(
		"-A", "--atlas-debug",
		type    = str,
		help    = "Save PNGs of generated texture pages to the specified directory for debugging/inspection",
		metavar = "path"
	)

	group = parser.add_argument_group("Configuration options")
	group.add_argument(
		"-s", "--set",
		action  = "append",
		type    = str,
		help    = "Override a default option (use JSON syntax to specify value)",
		metavar = "option=value"
	)
	group.add_argument(
		"-f", "--force-set",
		action  = "append",
		type    = str,
		help    = "Override an option for all entries, ignoring values in the config file (use JSON syntax to specify value)",
		metavar = "option=value"
	)

	group = parser.add_argument_group("File paths")
	group.add_argument(
		"configFile",
		type = FileType("rt"),
		help = "Path to JSON file containing a list of entries"
	)
	group.add_argument(
		"outputFile",
		type = FileType("wb"),
		help = "Path to bundle file to be generated"
	)

	return parser

def main():
	parser = _createParser()
	args   = parser.parse_args()

	with args.configFile as _file:
		#entryList = json.load(_file)
		entryList = parseJSON(_file.read())

	if type(entryList) is not list:
		parser.error("the root element of the JSON file is not a list")

	bundle  = BundleBuilder()
	options = DEFAULT_OPTIONS.copy()

	for arg in (args.set or []):
		key, value = arg.split("=", 1)
		options[key.strip().lower()] = json.loads(value)

	for _entry in entryList:
		# Inherit all default options and normalize the keys to lowercase.
		entry = options.copy()

		for key, value in _entry.items():
			entry[key.strip().lower()] = value

		for arg in (args.force_set or []):
			key, value = arg.split("=", 1)
			entry[key.strip().lower()] = json.loads(value)

		name  = entry["name"].strip()
		_from = os.path.normpath(entry["from"].strip())
		_type = entry["type"].strip().lower()

		# Add the asset to the bundle, preprocessing it if it's a background,
		# texture or sound or importing it as-is in other cases.
		match _type:
			case "texture" | "itexture":
				matchRegex = re.compile(entry["match"])
				skipFrames = int(entry["skipframes"]) + 1

				# Assume that the source file is a spritesheet containing
				# multiple animated textures (importImage() also treats single
				# images as spritesheets) and add each texture to the bundle
				# separately, skipping the ones that do not match the provided
				# regex.
				for textureName, frameList in importImage(_from):
					if textureName and not matchRegex.match(textureName):
						continue

					images  = []
					counter = -1

					for frame in frameList:
						# The counter is used to skip frames at regular intervals.
						if counter := ((counter + 1) % skipFrames):
							continue

						images.append(convertImage(frame, entry))

					if not images:
						logging.warning(f"({name}) can't import any frames for {textureName}")
						continue

					bundle.addTexture(
						name.format(name = textureName),
						images,
						_type
					)

			case "bg" | "ibg":
				entry["bpp"] = 16

				with Image.open(_from, "r") as _file:
					bundle.addBG(
						name,
						convertImage(_file, entry),
						int(entry["crop"][0]),
						int(entry["crop"][1]),
						_type
					)

			case "sound":
				with av.open(_from, "r") as _file:
					bundle.addSound(
						name,
						*convertSound(_file, entry),
						int(entry["samplerate"])
					)

			case _:
				with open(entry["from"], "rb") as _file:
					bundle.addEntry(name, _file.read(), _type)

	logging.info(f"processed {len(bundle.entries)} entries")

	pages = bundle.buildVRAM(
		args.discard_step,
		args.try_splits,
		args.preserve_palettes
	)

	for index, page in enumerate(pages):
		# Save all generated texpages (after expanding them back to 4/8bpp) as
		# grayscale images to the specified debug path if any. Note that this
		# loop has to be executed even if the pages aren't going to be saved,
		# due to buildVRAM() being a generator function.
		if args.atlas_debug:
			prefix = os.path.join(args.atlas_debug, f"{index:02d}")
			page4  = unpackNibbles2D(page) << 4

			#Image.fromarray(page,  "L").save(f"{prefix}_8.png")
			Image.fromarray(page4, "L").save(f"{prefix}_4.png")

	data = bundle.generate()
	logging.info(f"final bundle size: {len(data):7d} bytes")

	with args.outputFile as _file:
		_file.write(data)

if __name__ == "__main__":
	main()
