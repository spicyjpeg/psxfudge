# -*- coding: utf-8 -*-
# (C) 2022-2023 spicyjpeg

import logging
from collections import ChainMap
from itertools   import chain
from pathlib     import Path

import av, numpy
from PIL        import Image
from ..image    import convertImage
from ..audio    import convertSound
from ..builders import BundleBuilder
from ..parsers  import importKeyValue, importImages
from ..util     import unpackNibbles, iteratePaths, parseJSON, CaseDict
from .common    import IMAGE_PROPERTIES, SOUND_PROPERTIES, \
	STRING_TABLE_PROPERTIES, MultiEntryTool

## Tool classes

class _FudgeBundle(MultiEntryTool):
	def __init__(self):
		super().__init__(
			"fudgebundle",
			"Converts source assets listed in a JSON file and builds an asset "
			"bundle file.",
			IMAGE_PROPERTIES | SOUND_PROPERTIES | STRING_TABLE_PROPERTIES
		)

		self.addToolOptions()
		self.addConfigOptions()
		self.addFileOptions()
		self.addPackerOptions()

	def run(self, args, entryList, defaults, forced):
		bundle = BundleBuilder()
		logging.info(f"processing {len(entryList)} entries")

		for entryObj in entryList:
			entry = ChainMap(forced, CaseDict(entryObj), defaults)

			name  = entry["name"].strip()
			_from = tuple(iteratePaths(entry["from"]))
			_type = str(entry["type"]).strip().lower()

			# Add the asset to the bundle, preprocessing it if it's a texture or
			# sound or importing it as-is in other cases. If the type does not
			# match any known type, interpret it as a number.
			match _type:
				case "texture" | "itexture":
					# Assume that the source file is a spritesheet containing
					# multiple animated textures (importImages() also treats
					# single images as spritesheets) and add each texture to the
					# bundle separately.
					for _name, frameList in importImages(_from, entry):
						images = [
							tuple(convertImage(frame, entry))
							for frame in frameList
						]

						bundle.addTexture(
							name.format(sprite = _name),
							images,
							_type == "itexture"
						)

				case "bg" | "ibg":
					entry["bpp"] = 16

					if len(_from) > 1:
						logging.warning(f"({name}) more than one path specified, using only first path")

					with Image.open(_from[0], "r") as _file:
						bundle.addBG(
							name,
							next(convertImage(_file, entry)),
							int(entry["crop"][0]),
							int(entry["crop"][1]),
							_type == "ibg"
						)

				case "sound":
					if len(_from) > 1:
						logging.warning(f"({name}) more than one path specified, using only first path")

					with av.open(str(_from[0]), "r") as _file:
						bundle.addSound(name, convertSound(_file, entry))

				case "stringtable":
					bundle.addStringTable(
						name,
						importKeyValue(_from),
						entry["encoding"],
						int(entry["align"])
					)

				case "file":
					with open(entry["from"], "rb") as _file:
						bundle.addFile(name, _file.read())

				case _:
					with open(entry["from"], "rb") as _file:
						bundle.addEntry(name, _file.read(), int(_type, 0))

		logging.info(f"added {len(bundle.entries)} items to bundle")

		buckets = bundle.generate(
			args.discard_step,
			args.try_splits,
			args.preserve_palettes
		)

		if args.dump_atlas:
			# Place all generated texture pages side-by-side, convert the
			# resulting image from 4bpp to grayscale (ignoring palettes) and
			# save it to the given path.
			pages     = tuple(chain(*buckets))
			imageData = unpackNibbles(numpy.hstack(pages)) << 4

			try:
				Image.fromarray(imageData, "L").save(args.dump_atlas)
			except:
				logging.warning(f"failed to save atlas dump to {args.dump_atlas}")

		with args.outputFile.open("wb") as _file:
			for section in bundle.serialize():
				_file.write(section)

## Exports

fudgebundle = _FudgeBundle()

if __name__ == "__main__":
	fudgebundle()
