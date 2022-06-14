# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

"""
The texture packing algorithm implemented here is based on the rectpack2D
library, with some improvements and PS1-specific quirks (4/8bpp texture
rotation, texpage boundaries...) added. It should be possible to use it for
other purposes/platforms by writing a custom ImageWrapper class.

https://github.com/TeamHypersomnia/rectpack2D
"""

import logging

import numpy
from ._image import ImageWrapper

## Texture/palette packer

# Sorting doesn't take the images' color depths and packed widths into account.
SORT_ORDERS = {
	"area":         lambda image: image.width * image.height,
	"perimeter":    lambda image: (image.width + image.height) * 2,
	"longest":      lambda image: max(image.width, image.height),
	"width":        lambda image: image.width,
	"height":       lambda image: image.height,
	"pathological": lambda image: image.getPathologicalMult()
}

def _attemptPacking(images, atlasWidth, atlasHeight, altSplit):
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
		# packImages() or buildTexpages()
		if (_hash := image.getHash()) in hashes:
			_image = hashes[_hash]

			image.x    = _image.x
			image.y    = _image.y
			image.flip = _image.flip
			packed    += 1
			continue

		image.x = None
		image.y = None

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
			image.flip    = flip
			hashes[_hash] = image
			area         += width * height
			packed       += 1
			break

	return area, packed

def packImages(images, atlasWidth, atlasHeight, discardStep, trySplits):
	"""
	Takes a list of ImageWrapper objects and packs them in an atlas, setting
	their x, y and flip attributes (or leaving them set to None in case of
	failure). Returns a ( totalArea, numPackedImages ) tuple.
	"""

	splitModes  = ( False, True ) if trySplits else ( False, )
	highestArgs = None
	highestArea = 0

	for reverse in ( True, False ):
		for orderName, order in SORT_ORDERS.items():
			_images = sorted(images, key = order, reverse = reverse)

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
						packResults.append(
							_attemptPacking(_images, width, height, altSplit)
						)

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
						logging.debug(f"sort by {orderName} rev={reverse}: all images packed, aborting search")
						break

					if (
						(newWidth + step) > atlasWidth or
						(newHeight + step) > atlasHeight
					):
						logging.debug(f"sort by {orderName} rev={reverse}: can't extend atlas, aborting search")
						break

					newWidth  += step
					newHeight += step
				else:
					newWidth, newHeight = candidates[bestIndex % 4]

				step //= 2

			logging.debug(f"sort by {orderName} rev={reverse}: {newWidth}x{newHeight}, {packed} images packed")

			if area > highestArea:
				highestArgs = _images, newWidth, newHeight, (bestIndex > 3)
				highestArea = area

				# Stop trying other sorting algorithms if all images have been
				# packed.
				if packed == len(images):
					break

	if not highestArea:
		return 0, 0

	return _attemptPacking(*highestArgs)

def packPalettes(images, atlasWidth, atlasHeight, preserveLSB = False):
	"""
	Takes an iterable of ImageWrapper objects and returns a
	( newAtlas, freeHeight ) tuple containing an atlas with all palettes placed
	at the bottom. The px and py attributes of each image are also set to point
	to their respective palettes.
	"""

	_images = []
	hashes  = {} # hash: image
	px, py  = 0, atlasHeight - 1

	# Sort images by their BPP to make sure all 256-color palettes get packed
	# first.
	for image in sorted(
		images,
		key     = lambda image: image.bpp,
		reverse = True
	):
		if image.bpp == 16:
			continue

		# Remove duplicate/similar palettes by comparing their hashes. The LSB
		# of each RGB value is masked off (see getPaletteHash()) to remove
		# palettes that are close enough to another palette. As usual the
		# palettes have to be sorted ahead of time for this to work.
		if (_hash := image.getPaletteHash(preserveLSB)) in hashes:
			_image = hashes[_hash]

			image.px = _image.px
			image.py = _image.py
			continue

		image.px      = px // 16
		image.py      = py
		hashes[_hash] = image
		_images.append(image)

		px += 2 ** image.bpp
		py -= px // atlasWidth
		px %= atlasWidth

	if not _images:
		return

	atlas = numpy.zeros(( atlasHeight, atlasWidth * 2 ), numpy.uint8)
	for image in _images:
		image.blitPalette(atlas)

	logging.debug(f"packed {len(_images)} palettes")
	return atlas, py + (0 if px else 1)

## Texture page generator

TEXPAGE_WIDTH     =  64
TEXPAGE_MAX_WIDTH = 256
TEXPAGE_HEIGHT    = 256

def buildTexpages(
	images,
	discardStep      = 1,
	trySplits        = False,
	preservePalettes = False
):
	"""
	Takes an iterable of ImageWrapper objects, packs them and yields a series
	of NumPy arrays representing PS1 texture pages. The size (width) of each
	page may vary from 64 to 256 pixels, based on what needs to be packed.
	"""

	_images = images
	index   = 0

	while _images:
		atlasWidth = TEXPAGE_WIDTH
		failed     = []

		# Determine the width of this page by going through the remaining
		# images and checking if any of them has a 256-color palette to pack or
		# is potentially too wide to fit after packing.
		for image in _images:
			if index == 0 and image.bpp == 8:
				atlasWidth = TEXPAGE_MAX_WIDTH
				break

			while image.getPackedMaxSize()[0] > atlasWidth:
				atlasWidth *= 2

			if atlasWidth == TEXPAGE_MAX_WIDTH:
				break

		# If this is the first page being generated, gather all palettes and
		# pack them at the bottom. Otherwise create a new empty page.
		if index:
			freeHeight = TEXPAGE_HEIGHT
			atlas      = numpy.zeros(( freeHeight, atlasWidth * 2 ), numpy.uint8)
		else:
			atlas, freeHeight = packPalettes(
				images, atlasWidth, TEXPAGE_HEIGHT, preservePalettes
			)

		area, packed = packImages(
			_images, atlasWidth, freeHeight, discardStep, trySplits
		)

		if not packed:
			raise RuntimeError("packing failed, one or more images might be larger than maximum texpage size")

		# Collect the images that couldn't be packed and blit the other ones
		# onto the page.
		for image in _images:
			if image.x is None:
				failed.append(image)
				continue

			image.page = index
			image.blit(atlas)

		_images = failed
		index  += 1
		ratio   = 100 * area / (atlasWidth * freeHeight)

		logging.info(f"generated texpage {index} ({ratio:4.1f}% packing ratio, {len(failed)} images left)")
		yield atlas
