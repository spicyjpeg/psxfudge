# -*- coding: utf-8 -*-
# (C) 2022-2023 spicyjpeg

"""Hash table and bundle file builders

This module contains helper classes to generate PSXFudge hash tables and other
types of files that contain them (currently only bundles).
"""

import math, logging
from struct    import Struct
from enum      import IntEnum
from itertools import chain

from .packer import buildTexturePages
from .util   import alignToMultiple, alignMutableToMultiple, hash32, \
	bestHashTableLength

## Index file generator

INDEX_HEADER_STRUCT    = Struct("< 2H")
INDEX_ENTRY_STRUCT     = Struct("< I 2H")
INDEX_EXT_ENTRY_STRUCT = Struct("< 3I 2H")

class IndexBuilder:
	"""
	Builder class used to generate extended index (hash table) data. This data
	is used in several places (e.g. bundle headers) to store file/entry lookup
	tables.
	"""

	def __init__(self):
		self.entries = {}
		self.buckets = None
		self.chained = None

	def addEntry(self, name, offset, length = 0, entryType = 0):
		if (_hash := hash32(name)) in self.entries:
			raise IndexError(f"hash table already contains an entry named {name}")

		# INDEX_ENTRY_STRUCT is stored as a mutable list and not serialized
		# immediately as we may have to modify .next later in case of hash
		# collisions (see getHeader()).
		self.entries[_hash] = [ _hash, offset, length, entryType, 0 ]

	def _buildTable(self):
		# Round the optimal number of buckets up to the nearest power of 2 to
		# make it possible to improve lookup speed by calculating
		# "hash & (numBuckets - 1)" instead of "hash % numBuckets" (as modulo
		# is slow on the PS1).
		#numBuckets = bestHashTableLength(self.entries.keys())
		numBuckets = len(self.entries)
		numBuckets = 2 ** math.ceil(math.log2(numBuckets))

		self.buckets = [ None for _ in range(numBuckets) ]
		self.chained = []
		used = 0

		for _hash, entry in self.entries.items():
			hashMod = _hash % numBuckets

			if self.buckets[hashMod] is None:
				self.buckets[hashMod] = entry
				used += 1
				continue

			# If the bucket is already occupied, go through its chain to find
			# the last chained item then link the new entry to its .next.
			lastEntry = self.buckets[hashMod]
			while lastEntry[4]:
				lastEntry = self.chained[lastEntry[4] - numBuckets]

			lastEntry[4] = numBuckets + len(self.chained)
			self.chained.append(entry)

		logging.debug(f"hash table usage: {100 * used / numBuckets:.1f}% + {len(self.chained)} chained")

	def generate(self, extended = False):
		self._buildTable()

		length = len(self.buckets) + len(self.chained)
		if extended:
			length *= INDEX_EXT_ENTRY_STRUCT.size
		else:
			length *= INDEX_ENTRY_STRUCT.size

		return INDEX_HEADER_STRUCT.size + length

	def serialize(self, extended = False, globalOffset = 0):
		#self._buildTable()

		data = bytearray(INDEX_HEADER_STRUCT.pack(
			len(self.buckets), # .numBuckets
			len(self.chained)  # .numChained
		))

		for entry in chain(self.buckets, self.chained):
			if entry:
				_entry     = entry.copy()
				_entry[1] += globalOffset
			else:
				_entry = 0, 0, 0, 0, 0

			if extended:
				data.extend(INDEX_EXT_ENTRY_STRUCT.pack(*_entry))
			else:
				data.extend(INDEX_ENTRY_STRUCT.pack(
					_entry[0], # .hash
					_entry[1], # .offset
					_entry[4]  # .next
				))

		return data

## Bundle file generator

BUNDLE_HEADER_STRUCT  = Struct("< 7s B 4I 4B")
BUNDLE_HEADER_MAGIC   = b"fudgebn"
BUNDLE_HEADER_VERSION = 0x02

BG_HEADER_STRUCT      = Struct("< 4H")
TEXTURE_HEADER_STRUCT = Struct("< 4H")
TEXTURE_FRAME_STRUCT  = Struct("< 2H 6B H I")
SOUND_HEADER_STRUCT   = Struct("< 4H")

class EntryType(IntEnum):
	FILE         = 0x0000
	TEXTURE      = 0x0010 # Progressive animated texture
	ITEXTURE     = 0x0011 # Interlaced animated texture
	BG           = 0x0020 # Progressive background (in main RAM)
	IBG          = 0x0021 # Interlaced background (in main RAM)
	SOUND        = 0x0030
	STRING_TABLE = 0x0040
	CUSTOM       = 0x8000

DATA_SIZE      = 0x180000 # Approximately 1.5 MB for main data section
VRAM_DATA_SIZE = 0x100000 # 32 pages (not taking framebuffers into account)
SPU_DATA_SIZE  = 0x7d000
SECTOR_SIZE    = 0x800

class BundleBuilder(IndexBuilder):
	"""
	Class used (quite obviously) to build asset bundles. Bundle contents are
	always buffered in memory.
	"""

	def __init__(self):
		super().__init__()

		self.textures    = {}
		self.textureList = []

		self.header   = None
		self.vramData = bytearray()
		self.spuData  = bytearray()
		self.data     = bytearray()

	def addEntry(self, name, data, entryType = 0):
		if (len(self.data) + len(data)) > DATA_SIZE:
			raise RuntimeError("main RAM size limit exceeded")

		super().addEntry(name, len(self.data), len(data), entryType)
		self.data.extend(alignToMultiple(data, 4))

		logging.debug(f"({name}) type=0x{entryType:x}, offset=0x{len(self.data):x}")

	def addFile(self, name, data):
		self.addEntry(name, data, EntryType.FILE)

	def addTexture(self, name, images, interlaced = False):
		header = bytearray(TEXTURE_HEADER_STRUCT.pack(
			images[0][0].width,  # .width
			images[0][0].height, # .height
			len(images),         # .numFrames
			len(images[0])       # .numMipLevels
		))

		# Save the offset at which each frame's header is going to be placed.
		# As we don't yet know where each frame is going to be placed in VRAM,
		# we have to generate blank frame entries which will be filled in later
		# by _buildVRAM().
		for frame in images:
			for mipLevel in frame:
				if mipLevel.innerWidth > 255 or mipLevel.innerHeight > 255:
					raise RuntimeError("all frames' inner dimensions must be 255x255 or smaller")

				if interlaced:
					fields = mipLevel.toInterlaced(0), mipLevel.toInterlaced(1)
				else:
					fields = mipLevel,

				self.textures[len(self.data) + len(header)] = fields
				self.textureList.extend(fields)

				header.extend(bytes(TEXTURE_FRAME_STRUCT.size * len(fields)))

		self.addEntry(
			name,
			header,
			EntryType.ITEXTURE if interlaced else EntryType.TEXTURE
		)

	def addBG(self, name, image, x, y, interlaced = False):
		data = bytearray(BG_HEADER_STRUCT.pack(
			x, y, image.innerWidth, image.innerHeight
		))

		if interlaced:
			data.extend(image.toInterlaced(0).data)
			data.extend(image.toInterlaced(1).data)
		else:
			data.extend(image.data)

		self.addEntry(
			name,
			data,
			EntryType.IBG if interlaced else EntryType.BG
		)

	def addSound(self, name, sound):
		if sound.data.shape[0] > 2:
			raise RuntimeError("sounds must be mono or stereo")

		length      = sound.data.shape[1]
		leftOffset  = len(self.spuData)
		rightOffset = leftOffset

		if sound.data.shape[0] == 2:
			rightOffset += length
		if (rightOffset + length) > SPU_DATA_SIZE:
			raise RuntimeError("SPU RAM size limit exceeded")

		header = SOUND_HEADER_STRUCT.pack(
			leftOffset  // 8,         # .leftOffset
			rightOffset // 8,         # .rightOffset
			length      // 8,         # .length
			sound.getSPUSampleRate()  # .sampleRate
		)

		# Only the header is placed in the main data section. The actual ADPCM
		# data itself is appended to the SPU RAM section.
		self.addEntry(name, header, EntryType.SOUND)
		self.spuData.extend(sound.data)

	def addStringTable(self, name, entries, encoding, align):
		table   = IndexBuilder()
		blob    = bytearray()
		offsets = {}

		for key, value in entries.items():
			# Check if the string was already added to the blob.
			if value in offsets:
				table.addEntry(key, offsets[value])
				continue

			# Save the current length of the blob, then append the string and a
			# null terminator to it. This is basically a fancy implementation
			# of a stack-like allocator.
			offset = len(blob)
			blob.extend(value.encode(encoding))
			blob.append(0)

			if align:
				alignMutableToMultiple(blob, align)

			offsets[value] = offset
			table.addEntry(key, offset)

		length = table.generate(False)
		data   = table.serialize(False, length)
		data.extend(blob)

		self.addEntry(name, data, EntryType.STRING_TABLE)

	def _buildVRAM(self, *options, **kwOptions):
		buckets = buildTexturePages(self.textureList, *options, **kwOptions)

		for page in chain(*buckets):
			# FIXME: for some reason self.vramData.extend(page) only works if
			# there are no pages that have been split off from a >64x256 atlas
			self.vramData.extend(page.tobytes())

		if len(self.vramData) > VRAM_DATA_SIZE:
			raise RuntimeError("VRAM size limit exceeded")

		# Overwrite the dummy frame headers generated by addTexture() with the
		# proper data.
		for offset, fields in self.textures.items():
			for index, field in enumerate(fields):
				self.data[
					(offset + TEXTURE_FRAME_STRUCT.size * index):
					(offset + TEXTURE_FRAME_STRUCT.size * (index + 1))
				] = TEXTURE_FRAME_STRUCT.pack(
					field.page,             # .imagePage
					field.palettePage or 0, # .palettePage
					field.x,                # .imageX
					field.y,                # .imageY
					*field.margin,          # .marginX, .marginY
					field.innerWidth,       # .imageWidth
					field.innerHeight,      # .imageHeight
					field.getPaletteXY(),   # .paletteXY
					field.getFlags()        # .flags
				)

		return buckets

	def generate(self, *options, **kwOptions):
		super().generate(True)
		buckets = self._buildVRAM(*options, **kwOptions)

		# As in addTexture(), the header section needs to be populated with a
		# dummy header as the aligned lengths of each section are not yet known.
		self.header = bytearray(BUNDLE_HEADER_STRUCT.size)
		self.header.extend(super().serialize(True))

		for section in ( self.header, self.vramData, self.spuData, self.data ):
			alignMutableToMultiple(section, SECTOR_SIZE)

		lengths = len(self.vramData), len(self.spuData), len(self.data)

		self.header[0:BUNDLE_HEADER_STRUCT.size] = BUNDLE_HEADER_STRUCT.pack(
			BUNDLE_HEADER_MAGIC,   # .magic
			BUNDLE_HEADER_VERSION, # .version
			len(self.header),      # .headerLength
			*lengths,              # .sectionLengths
			*map(len, buckets)     # .atlasCounts
		)

		logging.info("uncompressed section sizes:")
		logging.info(f"  header:    {len(self.header):7d} bytes")
		logging.info(f"  VRAM data: {lengths[0]:7d} bytes ({100 * lengths[0] / VRAM_DATA_SIZE:4.1f}%)")
		logging.info(f"  SPU data:  {lengths[1]:7d} bytes ({100 * lengths[1] / SPU_DATA_SIZE:4.1f}%)")
		logging.info(f"  main data: {lengths[2]:7d} bytes ({100 * lengths[2] / DATA_SIZE:4.1f}%)")

		return buckets

	def serialize(self):
		yield from ( self.header, self.vramData, self.spuData, self.data )
