# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import os, math, logging

import numpy
from PIL      import Image
from ._util   import blitArray
from ._native import quantizeImage, toPS1ColorSpace, toPS1ColorSpace2D

## Image wrapper class (used by texture packer)

# TODO: add support for multiple palettes per image

TEXPAGE_WIDTH  =  64
TEXPAGE_HEIGHT = 256

class ImageWrapper:
	"""
	Wrapper class for converted images and palettes, holding placement data.
	"""

	def __init__(
		self,
		data,
		palette   = None,
		margin    = ( 0, 0 ),
		padding   = 0,
		flipModes = ( False, )
	):
		self.data      = data
		self.palette   = palette
		self.margin    = margin
		self.padding   = padding
		self.flipModes = flipModes

		self.height, self.width = data.shape

		# These attributes are set by the packing functions and used by the
		# blit*() methods.
		self.page        = None
		self.x,  self.y  = None, None
		self.px, self.py = None, None
		self.flip        = False

		#if data.dtype.itemsize == 2:
		if palette is None:
			self.bpp = 16
		else:
			self.bpp = 4 if (palette.size <= 32) else 8

	def getPackedSize(self, flip = False):
		scale = 16 // self.bpp

		if flip:
			width  = math.ceil((self.height + self.padding * 2) / scale)
			height = self.width + self.padding * 2
		else:
			width  = math.ceil((self.width + self.padding * 2) / scale)
			height = self.height + self.padding * 2

		return width, height

	def getPackedMaxSize(self):
		#widths, heights = zip(
			#self.getPackedSize(flip) for flip in self.flipModes
		#)

		#return max(widths), max(heights)
		return self.getPackedSize(self.flipModes[0])

	def getPathologicalMult(self):
		return (self.width * self.height) * \
			max(self.width, self.height) / min(self.width, self.height)

	def canBePlaced(self, x, y, flip = False):
		width, height = self.getPackedSize(flip)
		texpageWidth  = TEXPAGE_WIDTH * (self.bpp // 4)

		return \
			((x % texpageWidth)   + width)  <= texpageWidth and \
			((y % TEXPAGE_HEIGHT) + height) <= TEXPAGE_HEIGHT

	def getTexpageOverflow(self, x, y, flip = False):
		width, height = self.getPackedSize(flip)
		texpageWidth  = TEXPAGE_WIDTH * (self.bpp // 4)

		return (
			max(0, (x % texpageWidth)   + width  - texpageWidth),
			max(0, (y % TEXPAGE_HEIGHT) + height - TEXPAGE_HEIGHT)
		)

	def getHash(self):
		return hash(self.data.tobytes())

	def getPaletteHash(self, preserveLSB = False):
		# Drop the least significant bit of each color. As the hash is used for
		# deduplication, this means palettes that are "similar" enough to other
		# palettes will be removed from the atlas.
		data = self.palette.view(numpy.uint16)
		if not preserveLSB:
			data &= 0xfbde

		return hash(data.tobytes())

	def toInterlaced(self, field = 0):
		# Note that the packing/blitting attributes are *not* preserved and the
		# palette object is not duplicated.
		# https://numpy.org/doc/stable/user/basics.indexing.html#other-indexing-options
		return ImageWrapper(
			self.data[field::2, :],
			self.palette,
			self.margin,
			self.padding,
			self.flipModes
		)

	def getPackedData(self):
		if self.flip:
			data = numpy.rot90(self.data)
		else:
			data = self.data

		# Add padding on the left if any (this is the only way to get padding
		# right with 4bpp images), then cast the rotated image to a byte array
		# if it's 16bpp.
		data = numpy.c_[
			numpy.zeros(( data.shape[0], self.padding ), numpy.uint8),
			data
		]
		data = numpy.ascontiguousarray(data).view(numpy.uint8)

		# "Compress" 4bpp images by packing two pixels into each byte (NumPy
		# has no native support for 4-bit arrays, so a full byte is used for
		# each pixel even for <=16 colors). This is done by splitting the array
		# into vertically interlaced odd/even columns (after padding to ensure
		# the width is a multiple of 2) and binary OR-ing them after relocating
		# the odd columns' values to the upper nibble.
		if self.bpp == 4:
			if data.shape[1] % 2:
				data = numpy.c_[
					data,
					numpy.zeros(data.shape[0], numpy.uint8)
				]

			data = data[:, 0::2] | (data[:, 1::2] << 4)

		return data

	def blit(self, dest):
		data = self.getPackedData()
		blitArray(data, dest, ( self.y + self.padding, self.x * 2 ))

	def blitPalette(self, dest):
		width = 2 ** (self.bpp + 1)
		data  = self.palette.view(numpy.uint8).reshape(( 1, width ))
		blitArray(data, dest, ( self.py, self.px * 32 ))

## Image downscaler and quantizer

def _getMonoPalette(numSolid, numAlpha):
	return numpy.r_[
		numpy.linspace(
			start = ( 0x00, 0x00, 0x00, 0xff ),
			stop  = ( 0xff, 0xff, 0xff, 0xff ),
			num   = numSolid,
			dtype = numpy.uint8
		),
		numpy.linspace(
			start = ( 0x00, 0x00, 0x00, 0x80 ),
			stop  = ( 0xff, 0xff, 0xff, 0x80 ),
			num   = numAlpha,
			dtype = numpy.uint8
		),
		numpy.zeros(( 1, 4 ), numpy.uint8)
	]

PALETTES = {
	"auto":        None,
	"mono4":       _getMonoPalette( 15,   0), # 16 solid shades of gray
	"monoalpha4":  _getMonoPalette(  8,   7), # 8 solid shades + 7 semi-transparent shades
	"mono8":       _getMonoPalette(255,   0), # 256 solid shades of gray
	"monoalpha8":  _getMonoPalette(128, 127)  # 128 solid shades + 127 semi-transparent shades
}
SCALE_MODES = {
	"nearest":  Image.NEAREST,
	"lanczos":  Image.LANCZOS,
	"bilinear": Image.BILINEAR,
	"bicubic":  Image.BICUBIC,
	"box":      Image.BOX,
	"hamming":  Image.HAMMING
}
FLIP_MODES = {
	"none":            ( False, ),
	"flip":            ( True, ),
	"preferunflipped": ( False, True ),
	"preferflipped":   ( True, False )
}

def convertImage(image, options):
	"""
	Downscales and optionally quantizes a PIL image using the given dict of
	options. Returns an ImageWrapper object.
	"""

	name       = options.get("name", "image")
	crop       = map(int, options["crop"])
	scale      = float(options["scale"])
	bpp        = int(options["bpp"])
	palette    = PALETTES[options["palette"].lower()]
	dither     = float(options["dither"])
	scaleMode  = SCALE_MODES[options["scalemode"].lower()]
	alphaRange = map(int, options["alpharange"])
	blackValue = int(options["blackvalue"])
	padding    = int(options["padding"])
	flipModes  = FLIP_MODES[options["flipmode"].lower()]

	# Crop the image if necessary. Note that cropping is done before rescaling.
	x, y, width, height = crop
	_image = image.crop((
		x,
		y,
		min(x + width,  image.width),
		min(y + height, image.height)
	))

	# Throw an error if attempting to rescale an image that already has a
	# palette, since indexed color images can only be scaled using nearest
	# neighbor interpolation (and it generally doesn't make sense to do so).
	if scale != 1.0:
		if image.mode == "P":
			raise RuntimeError(f"({name}) can't rescale indexed color image")

		_image = _image.resize((
			int(_image.width  * scale),
			int(_image.height * scale)
		), scaleMode)

	# Trim any empty space around the image (but save the number of pixels
	# trimmed, so the margin can be restored on the PS1 side when drawing the
	# image). The padding option optionally re-adds an empty border around the
	# image as a workaround for GPU sampling quirks.
	margin = _image.getbbox()
	if margin is None:
		raise RuntimeError(f"({name}) image is empty")

	_image    = _image.crop(margin)
	data      = None
	numColors = 2 ** bpp

	if bpp == 16:
		if _image.mode == "P":
			logging.warning(f"({name}) converting indexed color back to 16bpp")

		data = toPS1ColorSpace2D(
			numpy.array(_image.convert("RGBA"), numpy.uint8),
			*alphaRange,
			blackValue
		)

		return ImageWrapper(data, None, margin[0:2], padding, flipModes)

	# If the image is in a suitable indexed format already, generate a 16x1 or
	# 256x1 bitmap out of its palette.
	if _image.mode == "P":
		if palette is not None:
			logging.warning(f"({name}) re-quantizing indexed color image to apply custom palette")
		else:
			# Calculate how many entries there are in the palette. Pillow makes
			# this non-trival for some reason.
			paletteData = _image.palette.tobytes()
			_numColors  = len(paletteData) // {
				"RGB":  3,
				"RGBA": 4,
				"L":    1
			}[_image.palette.mode]

			# If the number of entries is low enough, convert the palette to
			# RGBA format (by generating an Nx1 bitmap out of it) and from
			# there to a NumPy array.
			if _numColors > numColors:
				logging.warning(f"({name}) re-quantizing indexed color image due to existing palette being too large")
			else:
				logging.debug(f"({name}) image has a valid {_numColors}-color palette, skipping quantization")

				_palette = Image.frombytes(
					_image.palette.mode,
					( _numColors, 1 ),
					paletteData
				)
				_palette = numpy.array(_palette.convert("RGBA"), numpy.uint8)
				_palette = _palette.reshape(( _numColors, 4 ))
				data     = numpy.array(_image, numpy.uint8)

	# If the image is not indexed color or the palette is incompatible with the
	# desidered format (see above), quantize the image.
	# NOTE: I didn't use Pillow's built-in quantization functions as they are
	# crap and don't support RGBA images/palettes properly. Using libimagequant
	# manually (via the _native DLL) yields much better results.
	if data is None:
		_palette, data = quantizeImage(
			numpy.array(_image.convert("RGBA"), numpy.uint8),
			numColors,
			palette,
			5, # PS1 color depth (15bpp = 5bpp per channel)
			dither
		)

	# Pad the palette with null entries.
	_palette = numpy.r_[
		_palette,
		numpy.zeros(( numColors - _palette.shape[0], 4 ), numpy.uint8)
	]

	# Sort the palette (inaccurately) by the average brightness of each color
	# and remap the pixel data accordingly, then perform color space conversion
	# on the palette.
	mapping  = _palette.view(numpy.uint32).flatten().argsort()
	data     = mapping.argsort().astype(numpy.uint8)[data]
	_palette = toPS1ColorSpace(_palette[mapping], *alphaRange, blackValue)

	return ImageWrapper(data, _palette, margin[0:2], padding, flipModes)
