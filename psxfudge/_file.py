# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import os, math, logging
from struct    import Struct
from itertools import chain
from shutil    import copyfileobj
from tempfile  import SpooledTemporaryFile
from zlib      import crc32
from gzip      import GzipFile

from ._packer import buildTexpages
from ._util   import alignToMultiple, hash32, bestHashTableLength

## Index file generator

#INDEX_HEADER_MAGIC  = b"freaky on a friday night yea"
INDEX_HEADER_MAGIC  = b"[psxfudge bundle format]v1.0"
INDEX_HEADER_STRUCT = Struct("< 28s 4I 2H")
INDEX_ENTRY_STRUCT  = Struct("< 3I 2H")

class IndexBuilder:
	"""
	Builder class used to generate index (hash table) data. This data is used
	in several places (e.g. bundle headers) to store file/entry lookup tables.
	"""

	def __init__(self):
		self.entries = {}

	def addEntry(self, name, offset, length, entryType):
		if (_hash := hash32(name)) in self.entries:
			raise IndexError(f"hash table already contains an entry named {name}")

		# INDEX_ENTRY_STRUCT is stored as a mutable list and not serialized
		# immediately as we may have to modify .next later in case of hash
		# collisions (see getHeader()).
		self.entries[_hash] = [ _hash, offset, length, entryType, 0xffff ]

	def _buildTable(self):
		# Round the optimal number of buckets up to the nearest power of 2 to
		# make it possible to improve lookup speed by calculating
		# "hash & (numBuckets - 1)" instead of "hash % numBuckets" (as modulo
		# is slow on the PS1).
		#numBuckets = bestHashTableLength(self.entries.keys())
		numBuckets = len(self.entries)
		numBuckets = 2 ** math.ceil(math.log2(numBuckets))

		buckets = [ None for _ in range(numBuckets) ]
		chained = []
		used    = 0

		for _hash, entry in self.entries.items():
			hashMod = _hash % numBuckets

			if buckets[hashMod] is None:
				buckets[hashMod] = entry
				used += 1
				continue

			# If the bucket is already occupied, go through its chain to find
			# the last chained item then link the new entry to its .next.
			lastEntry = buckets[hashMod]
			while lastEntry[4] != 0xffff:
				lastEntry = chained[lastEntry[4] - numBuckets]

			lastEntry[4] = numBuckets + len(chained)
			chained.append(entry)

		logging.debug(f"hash table usage: {100 * used / numBuckets:.1f}% + {len(chained)} chained")
		return buckets, chained

	def generate(self, sectionLengths):
		buckets, chained = self._buildTable()
		length           = INDEX_HEADER_STRUCT.size + \
			INDEX_ENTRY_STRUCT.size * (len(buckets) + len(chained))

		data = bytearray(INDEX_HEADER_STRUCT.pack(
			INDEX_HEADER_MAGIC,               # .magic
			length,                           # .indexLength
			*(sectionLengths or ( 0, 0, 0 )), # .sectionLength[3]
			len(buckets),                     # .numBuckets
			len(chained)                      # .numChained
		))

		for entry in chain(buckets, chained):
			_entry = entry or ( 0, 0, 0, 0, 0xffff )
			data.extend(INDEX_ENTRY_STRUCT.pack(*_entry))

		return data

## Bundle file generator

BG_HEADER_STRUCT      = Struct("< 4H")
TEXTURE_HEADER_STRUCT = Struct("< 4B")
TEXTURE_FRAME_STRUCT  = Struct("< 6B H")
SOUND_HEADER_STRUCT   = Struct("< 4H")

ENTRY_TYPES = {
	"file":     0xf11e,
	"bundle":   0xda7a, # Currently unused
	"dll":      0xc0de,
	"texture":  0x0001, # Progressive animated texture
	"itexture": 0x8001, # Interlaced animated texture
	"bg":       0x0002, # Progressive background (in main RAM)
	"ibg":      0x8002, # Interlaced background (in main RAM)
	"sound":    0x0003
}

DATA_SIZE      = 0x180000    # Approximately 1.5 MB for main data section
VRAM_DATA_SIZE = 0x8000 * 20 # 20 texpages
SPU_DATA_SIZE  = 0x7a000
SECTOR_SIZE    = 0x800

class BundleBuilder(IndexBuilder):
	"""
	Class used (quite obviously) to build asset bundles. Bundle contents are
	always buffered in memory.
	"""

	def __init__(self):
		super().__init__()

		self.textures    = {}
		self.allTextures = []
		self.vramData    = bytearray()
		self.spuData     = bytearray()
		self.data        = bytearray()

	def addEntry(self, name, data, _type):
		if (len(self.data) + len(data)) > DATA_SIZE:
			raise RuntimeError("main RAM size limit exceeded")

		super().addEntry(
			name,
			len(self.data),
			len(data),
			ENTRY_TYPES.get(_type, 0xffff)
		)

		self.data.extend(alignToMultiple(data, 4))
		logging.debug(f"({name}) placed @ {len(self.data):08x}")

	def addTexture(self, name, images, _type = "texture"):
		if images[0].width > 255 or images[0].height > 255:
			raise RuntimeError("textures must be 255x255 or smaller")

		header = bytearray(TEXTURE_HEADER_STRUCT.pack(
			images[0].width,  # .width
			images[0].height, # .height
			images[0].bpp,    # .bpp
			len(images)       # .numFrames
		))

		# Save the offset at which each frame's header is going to be placed.
		# As we don't yet know where each frame is going to be placed in VRAM,
		# we have to generate blank frame entries which will be filled in later
		# by _buildVRAM().
		for image in images:
			if _type == "itexture":
				fields = image.toInterlaced(0), image.toInterlaced(1)
			else:
				fields = image,

			self.textures[len(self.data) + len(header)] = fields
			self.allTextures.extend(fields)

			header.extend(b"\x00" * TEXTURE_FRAME_STRUCT.size * len(fields))

		self.addEntry(name, header, _type)

	def addBG(self, name, image, x, y, _type = "bg"):
		data = bytearray(BG_HEADER_STRUCT.pack(
			x, y, image.width, image.height
		))

		if _type == "ibg":
			data.extend(image.toInterlaced(0).data.tobytes())
			data.extend(image.toInterlaced(1).data.tobytes())
		else:
			data.extend(image.data.tobytes())

		self.addEntry(name, data, _type)

	def addSound(self, name, data, rightOffset, sampleRate):
		if rightOffset:
			_rightOffset = len(self.spuData) + rightOffset
		else:
			_rightOffset = 0

		if (len(self.spuData) + len(data)) > SPU_DATA_SIZE or \
			(_rightOffset + len(data)) > SPU_DATA_SIZE:
			raise RuntimeError("SPU RAM size limit exceeded")

		header = SOUND_HEADER_STRUCT.pack(
			len(self.spuData) // 8,      # .leftOffset
			_rightOffset // 8,           # .rightOffset
			len(data) // 8,              # .length
			sampleRate * 0x1000 // 44100 # .sampleRate
		)

		# Only the header is placed in the main data section. The actual ADPCM
		# data itself is appended to the SPU RAM section.
		self.addEntry(name, header, "sound")
		self.spuData.extend(data)

	def buildVRAM(self, *options, **kwOptions):
		for page in buildTexpages(self.allTextures, *options, **kwOptions):
			# Reorder the page data into 64x256 sections (for larger pages) and
			# append it to the VRAM section.
			for offset in range(0, page.shape[1], 128):
				section = page[:, offset:(offset + 128)]
				self.vramData.extend(section.tobytes())

			yield page

		# Overwrite the dummy frame headers generated by addTexture() with the
		# proper data.
		for offset, fields in self.textures.items():
			for index, field in enumerate(fields):
				self.data[
					(offset + TEXTURE_FRAME_STRUCT.size * index):
					(offset + TEXTURE_FRAME_STRUCT.size * (index + 1))
				] = TEXTURE_FRAME_STRUCT.pack(
					field.x,       # .x
					field.y,       # .y
					*field.margin, # .marginX, .marginY
					field.page,    # .page
					field.flip,    # .flip
					(field.px // 16) | (field.py << 6) # .palette
				)

	def generate(self):
		lengths = len(self.vramData), len(self.spuData), len(self.data)
		header  = super().generate(lengths)

		logging.info("uncompressed section sizes:")
		logging.info(f"header:    {len(header):7d} bytes")
		logging.info(f"VRAM data: {lengths[0]:7d} bytes ({100 * lengths[0] / VRAM_DATA_SIZE:4.1f}%)")
		logging.info(f"SPU data:  {lengths[1]:7d} bytes ({100 * lengths[1] / SPU_DATA_SIZE:4.1f}%)")
		logging.info(f"main data: {lengths[2]:7d} bytes ({100 * lengths[2] / DATA_SIZE:4.1f}%)")

		return b"".join((
			alignToMultiple(section, SECTOR_SIZE)
			for section in ( header, self.vramData, self.spuData, self.data )
		))
