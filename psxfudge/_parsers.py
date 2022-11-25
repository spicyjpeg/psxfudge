# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import os, re, json, logging
from collections import defaultdict
from pathlib     import Path
from xml.etree   import ElementTree

from PIL    import Image
from ._util import parseRange, parseText, parseJSON, parseKeyValue, CaseDict

## Key-value file parser

KEY_VALUE_EXTENSIONS = CaseDict({
	".json": parseJSON,
	".txt":  parseKeyValue,
	".ini":  parseKeyValue,
	".lang": parseKeyValue
})

def importKeyValue(paths, constructor = dict):
	obj = constructor()

	for path in map(Path, paths):
		if path.suffix not in KEY_VALUE_EXTENSIONS:
			raise RuntimeError(f"unsupported extension for key-value files: {path.suffix}")

		with path.open("rt") as _file:
			obj.update(KEY_VALUE_EXTENSIONS[path.suffix](_file.read()))

	return obj

## Texture atlas parsers

ANIM_FRAME_REGEX  = re.compile(r"^(.+?)\s*([0-9]{1,4})$")
ATLAS_ENTRY_REGEX = re.compile(r"^\s*(.+?)\s*=\s*([0-9]+)\s*([0-9]+)\s*([0-9]+)\s*([0-9]+)", re.MULTILINE)

def _parseXMLAtlas(path):
	root = ElementTree.parse(path).getroot()

	for atlas in root.iter("TextureAtlas"):
		imagePath = atlas.get("imagePath", f"{path.stem}.png")

		# If the image path is not absolute, assume it is relative to the atlas
		# file's location.
		image   = Image.open(path.parent.joinpath(imagePath), "r")
		entries = defaultdict(dict)

		for texture in atlas.iter("SubTexture"):
			name   = texture.get("name")
			width  = int(texture.get("width", image.width))
			height = int(texture.get("height", image.height))

			entry = (
				int(texture.get("x", 0)),
				int(texture.get("y", 0)),
				width,
				height,
				-int(texture.get("frameX", 0)),
				-int(texture.get("frameY", 0)),
				int(texture.get("frameWidth", width)),
				int(texture.get("frameHeight", height)),
				True
			)

			# Split the frame number from the texture name.
			if _match := ANIM_FRAME_REGEX.match(name):
				name, frame = _match.groups()
				entries[name][int(frame)] = entry
			else:
				frames = entries[name]
				frames[len(frames)] = entry

		yield image, entries

def _parseJSONAtlas(path):
	with path.open("rt") as _file:
		root = parseJSON(_file.read())

	if type(root) is not list:
		root = root,

	for atlas in root:
		imagePath = atlas["meta"].get("image", f"{path.stem}.png")

		# If the image path is not absolute, assume it is relative to the atlas
		# file's location.
		image   = Image.open(path.parent.joinpath(imagePath), "r")
		entries = defaultdict(dict)

		for name, texture in atlas["frames"].items():
			source = texture["frame"]
			dest   = texture.get("spriteSourceSize", {})
			width  = int(source.get("w", image.width))
			height = int(source.get("h", image.height))

			if texture.get("rotated", False):
				raise RuntimeError("rotated subtextures are unsupported")

			entry = (
				int(source.get("x", 0)),
				int(source.get("y", 0)),
				width,
				height,
				int(dest.get("x", 0)),
				int(dest.get("y", 0)),
				int(dest.get("w", width)),
				int(dest.get("h", height)),
				texture.get("trimmed", False)
			)

			# Split the frame number from the texture name.
			if _match := ANIM_FRAME_REGEX.match(name):
				name, frame = _match.groups()
				entries[name][int(frame)] = entry
			else:
				frames = entries[name]
				frames[len(frames)] = entry

		yield image, entries

# This is a custom format used exclusively in Friday Night Funkin' Week 6 for
# some reason. Thankfully it's trivial to parse as it's just a text file with
# coordinates, but still... 3 different formats in a single game, wtf.
def _parseFNFAtlas(path):
	entries = defaultdict(dict)

	with open(path, "rt") as _file:
		atlas = parseText(_file.read(), "shell")

	for _match in ATLAS_ENTRY_REGEX.finditer(atlas):
		name, *coords = _match.groups()
		coords        = ( *map(int, coords), )

		entry = *coords, *coords, False

		# Split the frame number from the texture name.
		if "_" in name:
			name, frame = name.rsplit("_", 1)
			entries[name][int(frame)] = entry
		else:
			frames = entries[name]
			frames[len(frames)] = entry

	yield Image.open(path.parent.joinpath(f"{path.stem}.png"), "r"), entries

## Atlas and glob path handlers

ATLAS_EXTENSIONS = CaseDict({
	".xml":  _parseXMLAtlas,
	".json": _parseJSONAtlas,
	".txt":  _parseFNFAtlas,
	".ini":  _parseFNFAtlas
})

def _sortFrames(name, frames):
	firstFrame = min(frames.keys())
	lastFrame  = max(frames.keys())

	frame   = frames[firstFrame]
	missing = 0

	yield frame

	for index in range(firstFrame + 1, lastFrame + 1):
		# If this frame is not defined (i.e. some frame numbers are skipped),
		# reuse the last valid frame.
		if index in frames:
			frame = frames[index]
		else:
			missing += 1

		yield frame

	if missing:
		logging.debug(f"({name}) added {missing} missing frames")

def _processAtlas(atlas):
	counter = 0

	for image, entries in atlas:
		with image:
			for name, frames in entries.items():
				frameList = []

				for frame in _sortFrames(name, frames):
					(
						srcX, srcY, srcW, srcH,
						dstX, dstY, dstW, dstH, trim
					) = frame

					# Crop the frame from the source image, then place it onto
					# a "virtual canvas" to pad it with empty borders (if there
					# are any).
					cropped = image.crop(
						( srcX, srcY, srcX + srcW, srcY + srcH )
					)

					if trim and (dstX or dstY or dstW != srcW or dstH != srcH):
						canvas = Image.new(image.mode, ( dstW, dstH ))
						canvas.paste(cropped, ( dstX, dstY ))

						frameList.append(canvas)
					else:
						frameList.append(cropped)

				yield name, frameList
				counter += 1

	logging.debug(f"imported {counter} frame groups from atlas")

def _processGlob(globPath):
	if not (paths := sorted(globPath.parent.glob(globPath.name))):
		raise FileNotFoundError(f"no images found matching glob expression: {globPath}")

	entries = defaultdict(dict)

	# Group all frames that have the same prefix followed by a frame number
	# (e.g. character0001.png, character0002.png, etc.). Files that lack a
	# frame number are treated as a single-frame image.
	for path in paths:
		image = Image.open(path, "r")

		if _match := ANIM_FRAME_REGEX.match(path.stem):
			name, frame = _match.groups()
			entries[name][int(frame)] = image
		else:
			frames = entries[path.stem]
			frames[len(frames)] = image

	logging.debug(f"imported {len(entries)} frame groups using glob: {globPath}")

	for name, frames in entries.items():
		yield name, list(_sortFrames(name, frames))
			
## Image importer frontend

def importImages(paths, options):
	"""
	Loads one or more images (with glob path handling) or Adobe Animate atlas
	files, filters them by applying the given options and yields
	( name, frameList ) tuples, where each frame is a Pillow image object.
	"""

	matchRegex = re.compile(options["match"])
	frameRange = options["frames"]

	for path in map(Path, paths):
		# If the image is not an atlas, try interpreting the path as a glob
		# expression to find frames.
		if path.suffix in ATLAS_EXTENSIONS:
			images = _processAtlas(ATLAS_EXTENSIONS[path.suffix](path))
		else:
			images = _processGlob(path)

	for name, frameList in images:
		if not matchRegex.match(name):
			continue

		frames = tuple(map(
			frameList.__getitem__,
			parseRange(frameRange, 0, len(frameList) - 1)
		))

		if not frames:
			logging.warning(f"skipping all frames of texture {name}")
			continue

		yield name, frames
