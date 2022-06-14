# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import os, re, json
from xml.etree import ElementTree

from PIL    import Image
from ._util import globPaths, parseText, parseJSON

## Texture atlas parser

ANIM_FRAME_REGEX  = re.compile(r"^(.+?)\s*([0-9]{4})$")
ATLAS_ENTRY_REGEX = re.compile(r"^\s*(.+?)\s*=\s*([0-9]+)\s*([0-9]+)\s*([0-9]+)\s*([0-9]+)", re.MULTILINE)

def _parseXMLAtlas(path):
	atlasDir, base = os.path.split(path)
	atlasName      = os.path.splitext(base)[0]

	root = ElementTree.parse(path).getroot()

	for atlas in root.iter("TextureAtlas"):
		# If the image path is not absolute, assume it is relative to the atlas
		# file's location.
		imagePath = atlas.get("imagePath", f"{atlasName}.png")
		if not os.path.isabs(imagePath):
			imagePath = os.path.join(atlasDir, imagePath)

		image   = Image.open(imagePath, "r")
		entries = {}

		for texture in atlas.iter("SubTexture"):
			name = texture.get("name")

			# Split the frame number (4 digits) from the texture name.
			if _match := ANIM_FRAME_REGEX.match(name):
				name, frame = _match.groups()
			else:
				frame = 0

			if name not in entries:
				entries[name] = {}

			width  = int(texture.get("width", image.width))
			height = int(texture.get("height", image.height))

			entries[name][int(frame)] = (
				int(texture.get("x", 0)),
				int(texture.get("y", 0)),
				width,
				height,
				int(texture.get("frameX", 0)),
				int(texture.get("frameY", 0)),
				int(texture.get("frameWidth", width)),
				int(texture.get("frameHeight", height)),
				True
			)

		yield image, entries

def _parseJSONAtlas(path):
	atlasDir, base = os.path.split(path)
	atlasName      = os.path.splitext(base)[0]

	with open(path, "rt") as _file:
		root = parseJSON(_file.read())

	if type(root) is not list:
		root = root,

	for atlas in root:
		# If the image path is not absolute, assume it is relative to the atlas
		# file's location.
		imagePath = atlas["meta"].get("image", f"{atlasName}.png")
		if not os.path.isabs(imagePath):
			imagePath = os.path.join(atlasDir, imagePath)

		image   = Image.open(imagePath, "r")
		entries = {}

		for name, texture in atlas["frames"].items():
			source = texture["frame"]
			dest   = texture.get("spriteSourceSize", source)

			# Split the frame number (4 digits) from the texture name.
			if _match := ANIM_FRAME_REGEX.match(name):
				name, frame = _match.groups()
			else:
				frame = 0

			if name not in entries:
				entries[name] = {}

			if texture.get("rotated", False):
				raise RuntimeError("rotated subtextures are unsupported")

			width  = int(source.get("w", image.width))
			height = int(source.get("h", image.height))

			entries[name][int(frame)] = (
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

		yield image, entries

# This is a custom format used exclusively in week 6 for some reason.
# Thankfully it's trivial to parse as it's just a text file with coordinates,
# but still... 3 different formats in a single game, wtf.
def _parseFNFAtlas(path):
	atlasDir, base = os.path.split(path)
	atlasName      = os.path.splitext(base)[0]

	imagePath = os.path.join(atlasDir, f"{atlasName}.png")
	entries   = {}

	with open(path, "rt") as _file:
		atlas = parseText(_file.read(), "shell")

	for _match in ATLAS_ENTRY_REGEX.finditer(atlas):
		name, *coords = _match.groups()
		coords        = ( *map(int, coords), )

		# Split the frame number (which for some reason is not always 4 digits
		# here) from the texture name.
		if "_" in name:
			name, frame = name.split("_")
		else:
			frame = 0

		if name not in entries:
			entries[name] = {}

		entries[name][int(frame)] = (
			*coords,
			*coords,
			False
		)

	yield Image.open(imagePath, "r"), entries

## Image importer frontend

ATLAS_EXTENSIONS = {
	".xml":  _parseXMLAtlas,
	".json": _parseJSONAtlas,
	".txt":  _parseFNFAtlas,
	".ini":  _parseFNFAtlas
}

def importImage(path):
	"""
	Loads one or more images (by parsing a glob path) or an Adobe Animate atlas
	file and yields ( name, frameList ) tuples, where each frame is a Pillow
	image object.
	"""

	if (ext := os.path.splitext(path)[1].lower()) in ATLAS_EXTENSIONS:
		atlas = ATLAS_EXTENSIONS[ext](path)

		for image, entries in atlas:
			with image:
				for name, frames in entries.items():
					frameList = []
					numFrames = max(frames.keys()) + 1
					frame     = None

					for index in range(numFrames):
						# If this frame is not defined (i.e. the atlas skips some
						# frame numbers), reuse the last valid frame.
						frame = frames.get(index, frame)
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
							canvas.paste(cropped,
								( -dstX, -dstY, -dstX + srcW, -dstY + srcH )
							)

							frameList.append(canvas)
						else:
							frameList.append(cropped)

					yield name, frameList
	else:
		# If the image is not an atlas, try interpreting the path as a glob
		# expression to find multiple frames.
		if not (paths := sorted(globPaths(path))):
			raise FileNotFoundError(f"no images found matching path: {path}")

		# Name the animation after the first file found, stripping out the
		# frame number if present at the end of the file name.
		#base = os.path.split(paths[0])[1]
		#name = os.path.splitext(base)[0]
		#if _match := ANIM_FRAME_REGEX.match(name):
			#name = _match.group(1)

		#yield name, [ Image.open(_path, "r") for _path in paths ]
		yield None, [ Image.open(_path, "r") for _path in paths ]
