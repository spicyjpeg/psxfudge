# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import logging
from collections import ChainMap
from pathlib     import Path

import av
from PIL        import Image
from ..image    import convertImage
from ..audio    import convertSound
from ..builders import BundleBuilder
from ..parsers  import importKeyValue, importImages
from ..util     import unpackNibbles2D, iteratePaths, parseJSON, CaseDict
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
						bundle.addTexture(
							name.format(sprite = _name),
							[ convertImage(frame, entry) for frame in frameList ],
							_type == "itexture"
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

		pages = bundle.buildVRAM(
			args.discard_step,
			args.try_splits,
			args.preserve_palettes
		)

		for index, page in enumerate(pages):
			# Save all generated texpages (after expanding them back to 4/8bpp)
			# as grayscale images to the specified debug path if any. Note that
			# this loop has to be executed even if the pages aren't going to be
			# saved, due to buildVRAM() being a generator function.
			if args.atlas_debug:
				prefix = args.atlas_debug.joinpath(f"{index:02d}")
				page4  = unpackNibbles2D(page) << 4

				#Image.fromarray(page,  "L").save(f"{prefix}_8.png")
				Image.fromarray(page4, "L").save(f"{prefix}_4.png")

		bundle.generate()

		with args.outputFile.open("wb") as _file:
			for section in bundle.serialize():
				_file.write(section)

## Exports

fudgebundle = _FudgeBundle()

if __name__ == "__main__":
	fudgebundle()
