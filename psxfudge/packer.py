# -*- coding: utf-8 -*-
# (C) 2022-2023 spicyjpeg

"""Image packing algorithm implementation

The texture packing algorithm implemented here is based on the rectpack2D
library, with some improvements and PS1-specific quirks (4/8bpp texture
rotation, margins, texture page boundaries...) added.

https://github.com/TeamHypersomnia/rectpack2D
"""

import logging
from itertools import accumulate

import numpy
from .image import ImageWrapper

## Texture/palette packer

# Sorting doesn't take the images' color depths and packed widths into account.
SORT_ORDERS = (
	lambda image: image.innerWidth * image.innerHeight,
	lambda image: (image.innerWidth + image.innerHeight) * 2,
	lambda image: max(image.innerWidth, image.innerHeight),
	lambda image: image.innerWidth,
	lambda image: image.innerHeight,
	lambda image: image.getPathologicalMult()
)

def _attemptPacking(images, atlasWidth, atlasHeight, page, altSplit):
	# Start with a single empty space representing the entire atlas.
	spaces = [
		( 0, 0, atlasWidth, atlasHeight )
	]

	area   = 0
	packed = 0
	hashes = {} # hash: image

	for image in images:
		# Remove duplicate images by skipping an image if its hash matches the
		# one of another image. Note that hashing relies on palettes being
		# sorted with the same criteria across all images.
		# TODO: speed up packing by only performing this check ahead of time in
		# packImages() or buildAtlases()
		if (_hash := image.getHash()) in hashes:
			_image = hashes[_hash]

			image.x    = _image.x
			image.y    = _image.y
			image.page = _image.page
			image.flip = _image.flip
			packed    += 1
			continue

		image.x    = None
		image.y    = None
		image.page = None

		# Try placing the texture in either orientation. As the image's actual
		# width in the texture page depends on its color depth (i.e. indexed
		# color images are always squished horizontally), we have to calculate
		# it in either case.
		for flip in image.flipModes:
			width, height = image.getPackedSize(flip)

			# Find the smallest available space the image can be placed into.
			# This implementation is slightly different from rectpack2D as it
			# always goes through all empty spaces, which is inefficient but
			# might lead to better packing ratios, and ensures images are not
			# placed in the middle of the atlas (where they'd be split across
			# two different PS1 texture pages).
			lowestIndex  = None
			lowestOffset = None
			lowestMargin = 1e10

			for index, space in enumerate(spaces):
				x, y, maxWidth, maxHeight = space
				if width > maxWidth or height > maxHeight:
					continue

				# Try anchoring the image to all corners of the empty space
				# until it no longer crosses the texture page boundary. If no
				# corner is suitable, skip this empty space.
				offset  = None
				marginX = maxWidth  - width
				marginY = maxHeight - height

				for offsetX, offsetY in (
					( 0,       0 ),
					( marginX, 0 ),
					( 0,       marginY ),
					( marginX, marginY )
				):
					if image.canBePlaced(x + offsetX, y + offsetY, flip):
						offset = offsetX, offsetY
						break

				if offset is None:
					continue

				margin = (maxWidth * maxHeight) - (width * height)
				if margin < lowestMargin:
					lowestIndex  = index
					lowestOffset = offset
					lowestMargin = margin

			# If at least one suitable empty space was found, remove it from
			# the list and possibly replace with two smaller rectangles
			# representing the empty margins remaining after placement.
			if lowestIndex is None:
				continue

			x, y, maxWidth, maxHeight = spaces.pop(lowestIndex)
			offsetX, offsetY          = lowestOffset

			# There are quite a few potential cases here:
			# - Both dimensions match the available space's dimensions
			#   => add no new empty spaces
			# - Only one dimension equals the space's respective dimension
			#   => add a single space
			# - Both dimensions are smaller, and the image is not square
			#   => add two spaces, trying to keep both as close to a square as
			#      possible by using the image's longest side as a splitting
			#      axis (or the shortest side if altSplit = True)
			marginX = maxWidth  - width
			marginY = maxHeight - height
			padLeft = 0 if offsetX else width
			padTop  = 0 if offsetY else height

			if altSplit != (maxWidth * marginY < maxHeight * marginX):
				# Split along bottom side (horizontally)
				if marginY: spaces.insert(lowestIndex,
					( x, y + padTop, maxWidth, marginY )
				)
				if marginX: spaces.insert(lowestIndex,
					( x + padLeft, y + offsetY, marginX, height )
				)
			else:
				# Split along right side (vertically)
				if marginX: spaces.insert(lowestIndex,
					( x + padLeft, y, marginX, maxHeight )
				)
				if marginY: spaces.insert(lowestIndex,
					( x + offsetX, y + padTop, width, marginY )
				)

			image.x       = x + offsetX
			image.y       = y + offsetY
			image.page    = page
			image.flip    = flip
			hashes[_hash] = image

			area   += width * height
			packed += 1
			break

	return area, packed

def packImages(images, atlasWidth, atlasHeight, page, discardStep, trySplits):
	"""
	Takes a list of ImageWrapper objects and packs them in an atlas, setting
	their x, y, page and flip attributes (or leaving them set to None in case of
	failure). Returns a ( totalArea, numPackedImages ) tuple.
	"""

	splitModes  = ( False, True ) if trySplits else ( False, )
	highestArgs = None
	highestArea = 0

	for reverse in ( True, False ):
		for orderIndex, order in enumerate(SORT_ORDERS):
			logString = f"sort criterion {orderIndex}{' rev' if reverse else ''}"
			_images   = sorted(images, key = order, reverse = reverse)

			newWidth  = atlasWidth
			newHeight = atlasHeight
			packed    = None
			step      = min(atlasWidth, atlasHeight) // 2

			while step >= discardStep:
				# Try decreasing the width, height and both, and calculate the
				# packing ratio for each case.
				packResults = [] # packed, ratio

				altWidth   = newWidth  - step
				altHeight  = newHeight - step
				candidates = (
					( altWidth, altHeight ),
					( altWidth, newHeight ),
					( newWidth, altHeight ),
					( newWidth, newHeight )
				)

				for altSplit in splitModes:
					for width, height in candidates:
						results = _attemptPacking(
							_images, width, height, page, altSplit
						)
						packResults.append(results)

				# Find the case that led to the highest packing area. Stop once
				# the atlas can't be further shrunk down nor needs to be
				# enlarged, or if we're trying to exceed the maximum size.
				bestResult   = max(packResults)
				bestIndex    = packResults.index(bestResult) % 4
				area, packed = bestResult

				# If all attempts to shrink the size led to an increase in
				# failures, increase both dimensions by the current step and
				# try shrinking again; otherwise, accept the new sizes and
				# continue shrinking. Stop once all images have been packed or
				# if we're trying to exceed the maximum atlas size.
				if bestIndex == 3 or bestIndex == 7:
					if packed == len(images):
						logging.debug(f"{logString}: all images packed, aborting search")
						break

					if (
						(newWidth + step) > atlasWidth or
						(newHeight + step) > atlasHeight
					):
						logging.debug(f"{logString}: can't extend atlas, aborting search")
						break

					newWidth  += step
					newHeight += step
				else:
					newWidth, newHeight = candidates[bestIndex % 4]

				step //= 2

			logging.debug(f"{logString}: {newWidth}x{newHeight}, {packed} images packed")

			if area > highestArea:
				highestArgs = _images, newWidth, newHeight, page, (bestIndex > 3)
				highestArea = area

				# Stop trying other sorting algorithms if all images have been
				# packed.
				if packed == len(images):
					break

	if not highestArea:
		return 0, 0

	return _attemptPacking(*highestArgs)

def packPalettes(images, atlasWidth, atlasHeight, page, preserveLSB = False):
	"""
	Takes an iterable of ImageWrapper objects and packs their palettes along the
	bottom edge of an atlas, setting their px, py and palettePage attributes
	accordingly. Returns a ( freeHeight, numPackedPalettes ) tuple.
	"""

	hashes = {} # hash: image
	packed = 0
	px, py = 0, atlasHeight - 1

	# Sort images by their color depth to make sure all 256-color palettes get
	# packed first.
	for image in sorted(
		images,
		key     = lambda image: image.bpp,
		reverse = True
	):
		#if image.bpp == 16:
			#continue
		if (width := 2 ** image.bpp) > atlasWidth:
			continue

		# Remove duplicate/similar palettes by comparing their hashes. The LSB
		# of each RGB value is masked off (see getPaletteHash()) to remove
		# palettes that are close enough to another palette. As usual the
		# palettes have to be sorted ahead of time for this to work.
		_hash = image.getPaletteHash(preserveLSB)

		if (_image := hashes.get(_hash, None)) is not None:
			image.px          = _image.px
			image.py          = _image.py
			image.palettePage = page

			packed += 1
			continue

		image.px          = px // 16
		image.py          = py
		image.palettePage = page
		hashes[_hash]     = image

		packed += 1
		px     += width
		py     -= px // atlasWidth
		px     %= atlasWidth

		if py < 0:
			break

	return py + (0 if px else 1), packed

## Texture page generator

ATLAS_MIN_WIDTH = 64
ATLAS_MAX_WIDTH = 256
ATLAS_HEIGHT    = 256

def buildAtlases(
	images,
	discardStep      = 1,
	trySplits        = False,
	preservePalettes = False
):
	"""
	Takes an iterable of ImageWrapper objects, packs them and yields a series
	of NumPy arrays representing texture atlases. The size (width) of each
	atlas may vary from 64 to 256 pixels, depending on what needs to be packed.
	"""

	_images   = images
	_palettes = list(filter(lambda image: image.bpp != 16, images))
	index     = 0

	while _images or _palettes:
		atlasWidth = ATLAS_MIN_WIDTH

		# Pick an appropriate width for the atlas more or less heuristically,
		# based on which textures still need to be packed. This may fail
		# completely if the algorithm decides to prioritize smaller textures
		# (thus producing an unnecessarily large atlas), but that does not
		# usually happen.
		for image in _palettes:
			if image.bpp == 8:
				atlasWidth = ATLAS_MAX_WIDTH
				break

		if atlasWidth < ATLAS_MAX_WIDTH:
			for image in _images:
				while atlasWidth < image.getPackedMaxWidth():
					atlasWidth += ATLAS_MIN_WIDTH

					if atlasWidth == ATLAS_MAX_WIDTH:
						break

		freeHeight, packedPalettes = packPalettes(
			_palettes, atlasWidth, ATLAS_HEIGHT, index, preservePalettes
		)
		area, packedImages = packImages(
			_images, atlasWidth, freeHeight, index, discardStep, trySplits
		)

		if not packedPalettes and not packedImages:
			raise RuntimeError("packing failed, attempted to generate an empty atlas")

		# Collect the images and palettes that couldn't be packed and blit
		# everything else onto the atlas.
		atlas = numpy.zeros(( ATLAS_HEIGHT, atlasWidth * 2 ), numpy.uint8)

		unpackedImages   = []
		unpackedPalettes = []

		for image in _images:
			if image.page is None:
				unpackedImages.append(image)
			else:
				image.blit(atlas)

		for image in _palettes:
			if image.palettePage is None:
				unpackedPalettes.append(image)
			else:
				image.blitPalette(atlas)

		_images    = unpackedImages
		_palettes  = unpackedPalettes
		index     += 1
		ratio      = 100 * area / (atlasWidth * freeHeight)

		logging.info(f"atlas {index}: {atlasWidth}x{ATLAS_HEIGHT}, {ratio:4.1f}% packing ratio, {packedImages}/{len(_images)} images, {packedPalettes}/{len(_palettes)} palettes")
		yield atlas

def buildTexturePages(
	images,
	discardStep      = 1,
	trySplits        = False,
	preservePalettes = False
):
	"""
	A wrapper around buildAtlases() that splits up multi-page atlases into
	individual texture pages, sorts them by their width and fixes the page and
	palettePage attributes of each image. Returns a list of buckets (lists of
	texture pages), sorted by the respective atlases' widths in decreasing
	order.
	"""

	numTypes = ATLAS_MAX_WIDTH // ATLAS_MIN_WIDTH
	buckets  = [ [] for _ in range(numTypes) ]
	indexMap = {} # index: bucketIndex, pageOffset

	# Sort all atlases into one of four "buckets" (256x256, 192x256, 128x256,
	# 64x256) and build a mapping of which atlas went into which bucket and how
	# many texture pages were already present in that bucket.
	for index, atlas in enumerate(
		buildAtlases(images, discardStep, trySplits, preservePalettes)
	):
		width       = atlas.shape[1]
		bucketIndex = numTypes - width // (ATLAS_MIN_WIDTH * 2)

		bucket          = buckets[bucketIndex]
		indexMap[index] = bucketIndex, len(bucket)

		# Split the atlas into 64x256 texture pages.
		for offset in range(0, width, ATLAS_MIN_WIDTH * 2):
			bucket.append(atlas[:, offset:(offset + ATLAS_MIN_WIDTH * 2)])

	# As all buckets are going to be concatenated, calculate the texture page
	# offset each bucket is going to end up at, then derive the absolute texture
	# page offset for each image and palette.
	bucketOffsets = tuple(accumulate(map(len, buckets), initial = 0))

	for image in images:
		bucket, pageOffset = indexMap[image.page]
		image.page         = bucketOffsets[bucket] + pageOffset

		if image.palettePage is not None:
			bucket, pageOffset = indexMap[image.palettePage]
			image.palettePage  = bucketOffsets[bucket] + pageOffset

	return buckets
