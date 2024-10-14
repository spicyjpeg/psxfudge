# -*- coding: utf-8 -*-
# (C) 2022-2023 spicyjpeg

"""Image conversion module

This module defines the ImageWrapper class to hold converted image and palette
data, as well as functions to generate an ImageWrapper from a PIL/Pillow image
object.
"""

import math, logging
from struct import Struct
from enum   import IntEnum

import numpy
from PIL      import Image
from .util   import blitArray, cropArray, CaseDict
from .native import quantizeImage, toPS1ColorSpace, toPS1ColorSpace2D

## Image wrapper class (used by texture packer)

# TODO: add support for multiple palettes per image

TEXPAGE_WIDTH  =  64
TEXPAGE_HEIGHT = 256

TIM_HEADER_STRUCT  = Struct("< 2I")
TIM_HEADER_VERSION = 0x10
TIM_SECTION_STRUCT = Struct("< I 4H")

class ImageFlags(IntEnum):
	BPP_4          = 0 << 0
	BPP_8          = 1 << 0
	BPP_16         = 2 << 0
	INTERLACE_EVEN = 1 << 2
	INTERLACE_ODD  = 2 << 2
	HAS_MARGIN     = 1 << 4
	FLIP           = 1 << 5

class ImageWrapper:
	"""
	Wrapper class for converted images and palettes, holding metadata such as
	placement information alongside image data.
	"""

	def __init__(
		self,
		data,
		palette     = None,
		leftMargin  = ( 0, 0 ),
		rightMargin = ( 0, 0 ),
		padding     = 0,
		flipModes   = ( False, ),
		field       = None
	):
		if data.ndim != 2:
			raise ValueError("image data must be 2-dimensional")
		if palette is not None and palette.ndim != 1:
			raise ValueError("palette data must be 1-dimensional")

		self.data      = data
		self.palette   = palette
		self.margin    = leftMargin
		self.padding   = padding
		self.flipModes = flipModes
		self.field     = field

		self.innerHeight, self.innerWidth = data.shape
		self.width  = leftMargin[0] + self.innerWidth  + rightMargin[0]
		self.height = leftMargin[1] + self.innerHeight + rightMargin[1]

		# These attributes are set by the packing functions and used by the
		# blit*() methods.
		self.page        = None
		self.palettePage = None
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
			width  = math.ceil((self.innerHeight + self.padding * 2) / scale)
			height = self.innerWidth + self.padding * 2
		else:
			width  = math.ceil((self.innerWidth + self.padding * 2) / scale)
			height = self.innerHeight + self.padding * 2

		return width, height

	def getPackedMaxWidth(self):
		return max(self.getPackedSize(flip)[0] for flip in self.flipModes)

	def getPathologicalMult(self):
		return \
			(self.innerWidth * self.innerHeight) * \
			max(self.innerWidth, self.innerHeight) / \
			min(self.innerWidth, self.innerHeight)

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

	def getPaletteXY(self):
		if self.palettePage is None:
			return 0

		return (self.px // 16) | (self.py << 6)

	def getFlags(self):
		flags = {
			4:  ImageFlags.BPP_4,
			8:  ImageFlags.BPP_8,
			16: ImageFlags.BPP_16
		}[self.bpp]

		if self.field is not None:
			flags |= ImageFlags.INTERLACE_ODD if self.field \
				else ImageFlags.INTERLACE_EVEN
		if self.innerWidth < self.width or self.innerHeight < self.height:
			flags |= ImageFlags.HAS_MARGIN
		if self.flip:
			flags |= ImageFlags.FLIP

		return flags

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
			self.flipModes,
			field
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
		# the width is a multiple of 4) and binary OR-ing them after relocating
		# the odd columns' values to the upper nibble.
		if self.bpp == 4:
			if (align := (data.shape[1] % 4)):
				data = numpy.c_[
					data,
					numpy.zeros(( data.shape[0], 4 - align ), numpy.uint8)
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

	def toTIM(self):
		tim = bytearray(TIM_HEADER_STRUCT.pack(
			TIM_HEADER_VERSION,
			# Bit 3 signals the presence of a palette section in the file
			{ 4: 0x08, 8: 0x09, 16: 0x02 }[self.bpp]
		))

		# Generate the palette section if any.
		if self.bpp != 16:
			if int(self.px) % 16:
				logging.warning("palette X offset is not aligned to 16 pixels")

			paletteData = self.palette.view(numpy.uint16)
			tim.extend(TIM_SECTION_STRUCT.pack(
				TIM_SECTION_STRUCT.size + paletteData.size * 2,
				self.px,
				self.py,
				paletteData.size,
				1
			))
			tim.extend(paletteData)

		# Generate the image section.
		imageData = self.getPackedData()
		tim.extend(TIM_SECTION_STRUCT.pack(
			TIM_SECTION_STRUCT.size + imageData.size,
			int(self.x),
			int(self.y),
			*self.getPackedSize()
		))
		tim.extend(imageData)

		return tim

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

PALETTES = CaseDict({
	"auto":        None,
	"mono4":       _getMonoPalette( 15,   0), # 16 solid shades of gray
	"monoAlpha4":  _getMonoPalette(  8,   7), # 8 solid shades + 7 semi-transparent shades
	"mono8":       _getMonoPalette(255,   0), # 256 solid shades of gray
	"monoAlpha8":  _getMonoPalette(128, 127)  # 128 solid shades + 127 semi-transparent shades
})
SCALE_MODES = CaseDict({
	"nearest":  Image.NEAREST,
	"lanczos":  Image.LANCZOS,
	"bilinear": Image.BILINEAR,
	"bicubic":  Image.BICUBIC,
	"box":      Image.BOX,
	"hamming":  Image.HAMMING
})
FLIP_MODES = CaseDict({
	"none":            ( False, ),
	"flip":            ( True, ),
	"preferUnflipped": ( False, True ),
	"preferFlipped":   ( True, False )
})

def _processExistingPalette(name, image, maxNumColors, customPalette):
	if image.mode != "P":
		return None, None

	if customPalette is not None:
		logging.warning(f"({name}) re-quantizing indexed color image to apply custom palette")
		return None, None

	# Calculate how many entries there are in the palette. Pillow makes this
	# non-trival for some reason.
	paletteData = image.palette.tobytes()
	numColors   = len(paletteData) // {
		"RGB":  3,
		"RGBA": 4,
		"L":    1
	}[image.palette.mode]

	# If the number of entries is low enough, convert the palette to RGBA format
	# (by generating an Nx1 bitmap out of it) and from there to a NumPy array.
	if numColors > maxNumColors:
		logging.warning(f"({name}) re-quantizing indexed color image due to existing palette being too large")
		return None, None

	logging.debug(f"({name}) image has valid {_numColors}-color palette, skipping quantization")

	palette = Image.frombytes(image.palette.mode, ( numColors, 1 ), paletteData)
	palette = numpy.array(palette.convert("RGBA"), numpy.uint8)
	palette = palette.reshape(( _numColors, 4 ))

	return palette, numpy.array(image, numpy.uint8)

def convertImage(image, options):
	"""
	Downscales and optionally quantizes a PIL image using the given dict of
	options. Yields a series of ImageWrapper objects, each representing a mipmap
	level.
	"""

	name       = options.get("name", "<unknown>")
	mipLevels  = int(options["mipLevels"])
	crop       = map(int, options["crop"])
	scale      = float(options["scale"])
	mipScale   = float(options["mipScale"])
	bpp        = int(options["bpp"])
	palette    = PALETTES[options["palette"]]
	dither     = float(options["dither"])
	scaleMode  = SCALE_MODES[options["scaleMode"]]
	alphaRange = sorted(map(int, options["alphaRange"]))
	blackValue = tuple(map(int, options["blackValue"]))
	cropMode   = options["cropMode"].strip().lower()
	padding    = int(options["padding"])
	flipModes  = FLIP_MODES[options["flipMode"]]

	# Crop the image if necessary. Note that cropping is done before rescaling.
	x, y, width, height = crop
	_image = image.crop((
		x,
		y,
		min(x + width,  image.width),
		min(y + height, image.height)
	))

	numColors  = 2 ** bpp
	blackRepl  = (blackValue[0] & 31)
	blackRepl |= (blackValue[1] & 31) << 5
	blackRepl |= (blackValue[2] & 31) << 10
	blackRepl |= (blackValue[3] &  1) << 15

	for mipLevel in range(mipLevels):
		# Throw an error if attempting to rescale an image that already has a
		# palette, since indexed color images can only be scaled using nearest
		# neighbor interpolation (and it generally doesn't make sense to do so).
		# TODO: handle this in a "better" way for mipmapped images
		if scale == 1.0:
			scaledImage = _image
		elif _image.mode == "P":
			raise RuntimeError(f"({name}) can't rescale indexed color image")
		else:
			scaledImage = _image.resize((
				int(_image.width  * scale),
				int(_image.height * scale)
			), scaleMode)

		if bpp == 16:
			if scaledImage.mode == "P":
				logging.warning(f"({name}) converting indexed color back to 16bpp")

			imageData   = numpy.array(scaledImage.convert("RGBA"), numpy.uint8)
			imageData   = toPS1ColorSpace2D(imageData, *alphaRange, blackRepl)
			paletteData = None
		else:
			paletteData, imageData = _processExistingPalette(
				name, scaledImage, numColors, palette
			)

			# If the image is not indexed color or the palette is incompatible
			# with the desidered format (see above), quantize the image.
			if imageData is None:
				paletteData, imageData = quantizeImage(
					numpy.array(scaledImage.convert("RGBA"), numpy.uint8),
					numColors,
					palette,
					5, # PS1 color depth (15bpp = 5bpp per channel)
					dither
				)

			# Pad the palette with null entries and sort it by each color's
			# packed RGB value, remapping the pixel data accordingly.

			paletteData = numpy.r_[
				paletteData,
				numpy.zeros(( numColors - paletteData.shape[0], 4 ), numpy.uint8)
			]

			mapping     = paletteData.view(numpy.uint32).flatten().argsort()
			imageData   = mapping.argsort().astype(numpy.uint8)[imageData]
			paletteData = toPS1ColorSpace(
				paletteData[mapping], *alphaRange, blackRepl
			)

		# Trim any empty borders around the image (but save the number of pixels
		# trimmed when cropMode = "preserveMargin", so the margin can be
		# restored when drawing the image). The padding option optionally adds a
		# new empty border around the image as a workaround for GPU sampling
		# quirks.
		if cropMode in ( "preservemargin", "removemargin" ):
			imageData, leftMargin, rightMargin = cropArray(imageData)
		if cropMode != "preservemargin":
			leftMargin  = 0, 0
			rightMargin = 0, 0

		scale *= mipScale

		yield ImageWrapper(
			imageData, paletteData, leftMargin, rightMargin, padding, flipModes
		)
