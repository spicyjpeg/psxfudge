#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import os, re, logging, json
from struct      import Struct
from collections import ChainMap
from pathlib     import Path
from argparse    import FileType

import numpy, av
from PIL        import Image
from ._builders import BundleBuilder
from ._image    import convertImage
from ._parsers  import importKeyValue, importImages
from ._avenc    import convertSound
from ._util     import unpackNibbles2D, normalizePaths, parseJSON, CaseDict, ArgParser

DEFAULT_PROPERTIES = {
	# Image options
	"match":       ".*",
	"frames":      "0-255",
	"crop":        ( 0, 0, 1e10, 1e10 ),
	"scale":       1.0,
	"bpp":         4,
	"palette":     "auto",
	"dither":      0.5,
	"scaleMode":   "lanczos",
	"alphaRange":  ( 0x20, 0xe0 ),
	"blackValue":  ( 1, 1, 1, 0 ),
	"cropMode":    "preserveMargin",
	"padding":     0,
	"flipMode":    "preferUnflipped",
	# Sound options
	"chunkLength": 0x6800,
	"sampleRate":  0,
	"channels":    1,
	"loopOffset":  -1.0,
	# String table options
	"encoding":    "ascii",
	"align":       4
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
		type    = Path,
		help    = "Save PNGs of generated texture pages to the specified directory for debugging/inspection",
		metavar = "path"
	)

	group = parser.add_argument_group("Configuration options")
	group.add_argument(
		"-s", "--set",
		action  = "append",
		type    = str,
		help    = "Set a property for all entries except ones that override it (use JSON syntax to specify value)",
		metavar = "property=value"
	)
	group.add_argument(
		"-f", "--force-set",
		action  = "append",
		type    = str,
		help    = "Override a property for all entries, ignoring values in the config file (use JSON syntax to specify value)",
		metavar = "property=value"
	)

	group = parser.add_argument_group("File paths")
	group.add_argument(
		"configFile",
		type  = FileType("rt"),
		nargs = "+",
		help  = "Paths to JSON files containing a list of entry objects"
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

	entryList = []

	for _file in args.configFile:
		with _file:
			_list = parseJSON(_file.read())
		if type(_list) is not list:
			parser.error(f"the root element of {_file.name} is not a list")

		entryList.extend(_list)

	bundle  = BundleBuilder()
	options = CaseDict(DEFAULT_PROPERTIES)
	forced  = CaseDict()

	for arg in (args.set or []):
		key, value = arg.split("=", 1)
		options[key] = json.loads(value)
	for arg in (args.force_set or []):
		key, value = arg.split("=", 1)
		forced[key] = json.loads(value)

	for _entry in entryList:
		# Inherit all default options and normalize the keys to lowercase.
		entry = ChainMap(forced, CaseDict(_entry), options)

		name  = entry["name"].strip()
		_from = tuple(normalizePaths(entry["from"]))
		_type = entry["type"].strip().lower()

		# Add the asset to the bundle, preprocessing it if it's a background,
		# texture or sound or importing it as-is in other cases.
		match _type:
			case "texture" | "itexture":
				# Assume that the source file is a spritesheet containing
				# multiple animated textures (importImages() also treats single
				# images as spritesheets) and add each texture to the bundle
				# separately.
				for _name, frameList in importImages(_from, entry):
					bundle.addTexture(
						name.format(sprite = _name),
						[ convertImage(frame, entry) for frame in frameList ],
						_type
					)

			case "bg" | "ibg":
				entry["bpp"] = 16

				if len(_from) > 1:
					logging.warning(f"({name}) more than one path specified, using only first path")

				with Image.open(_from[0], "r") as _file:
					bundle.addBG(
						name,
						convertImage(_file, entry),
						int(entry["crop"][0]),
						int(entry["crop"][1]),
						_type
					)

			case "sound":
				if len(_from) > 1:
					logging.warning(f"({name}) more than one path specified, using only first path")

				with av.open(_from[0], "r") as _file:
					bundle.addSound(name, *convertSound(_file, entry))

			case "stringtable":
				bundle.addStringTable(
					name,
					importKeyValue(_from),
					entry["encoding"],
					int(entry["align"])
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
			prefix = args.atlas_debug.joinpath(f"{index:02d}")
			page4  = unpackNibbles2D(page) << 4

			#Image.fromarray(page,  "L").save(f"{prefix}_8.png")
			Image.fromarray(page4, "L").save(f"{prefix}_4.png")

	bundle.generate()

	with args.outputFile as _file:
		for section in bundle.serialize():
			_file.write(section)

if __name__ == "__main__":
	main()
