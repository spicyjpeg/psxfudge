# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import re, math, json
from time        import gmtime
from pathlib     import Path
from collections import UserDict
from ast         import literal_eval
from struct      import Struct
from tempfile    import mkdtemp

import numpy

## Array/string/iterator utilities

def blitArray(source, dest, position):
	pos = (  (x if x >= 0 else None) for x in position )
	neg = ( (-x if x  < 0 else None) for x in position )

	destView = dest[tuple(
		slice(x, None) for x in pos
	)]
	sourceView = source[tuple(
		slice(*args) for args in zip(neg, destView.shape)
	)]

	destView[tuple(
		slice(None, x) for x in source.shape
	)] = sourceView

def unpackNibbles2D(data, highNibbleFirst = False):
	"""
	Unpacks the low and high nibbles in a NumPy 2D array of bytes and returns
	a new array whose width is doubled.
	"""

	if data.ndim != 2:
		raise ValueError("source array must be 2D")

	unpacked = numpy.zeros((
		data.shape[0],
		data.shape[1] * 2
	), data.dtype)

	unpacked[:, (1 if highNibbleFirst else 0)::2] = data & 0xf
	unpacked[:, (0 if highNibbleFirst else 1)::2] = (data >> 4) & 0xf

	return unpacked

def alignToMultiple(data, length, padding = b"\x00"):
	"""
	Pads a string or byte string with the given padding until its length is a
	multiple of the specified length.
	"""

	chunks = math.ceil(len(data) / length)
	return data.ljust(chunks * length, padding)

def alignMutableToMultiple(obj, length, padding = b"\x00"):
	"""
	Pads an array or other mutable sequence in-place with the given padding
	until its length is a multiple of the specified length.
	"""

	remaining = length - (len(obj) % length)
	if remaining < length:
		obj.extend(padding * remaining)

def closestHigherMultiple(x, length):
	"""
	Returns the first multipe of `length` higher than `x`
	"""
	return math.ceil(x /  length) * length

def hash32(obj):
	"""
	Returns the 32-bit "sdbm hash" of a string, byte array or other iterable.

	http://www.cse.yorku.ca/~oz/hash.html
	"""

	if type(obj) is int:
		return obj

	value = 0

	for item in obj:
		if type(item) is str:
			byte = ord(item)
		else:
			byte = int(item) & 0xff

		value = (
			byte + \
			((value <<  6) & 0xffffffff) + \
			((value << 16) & 0xffffffff) - \
			value
		) & 0xffffffff

	return value

def swapEndianness(value, bits = 32):
	_value, output = value, 0

	for _ in range(0, bits, 8):
		output   = (output << 8) | (_value & 0xff)
		_value >>= 8

	return output

## Format conversion

def toMSDOSTime(unixTime = None):
	_time = gmtime(unixTime)
	if _time.tm_year < 1980:
		raise ValueError("invalid year for MS-DOS time format")

	value  = _time.tm_sec  // 2
	value |= _time.tm_min  << 5
	value |= _time.tm_hour << 11
	value |= _time.tm_mday << 16
	value |= _time.tm_mon  << 21
	value |= (_time.tm_year - 1980) << 25

	return value

## String manipulation

RANGE_ITEM_REGEX = re.compile(r"([0-9]+)(?:\s*?-\s*?([0-9]+)(?:\s*?:\s*?([0-9]+))?)?")

def _isWithinBounds(value, minValue = None, maxValue = None):
	if minValue is not None and value < minValue:
		return False
	if maxValue is not None and value > maxValue:
		return False

	return True

def parseRange(_range, minValue = None, maxValue = None):
	"""
	Parses a string containing space-delimited positive integers, optionally
	with dashes specifying ranges and colons prefixing strides (e.g.
	"1 8-10 3-7:2") and yields all values (e.g. [ 1, 8, 9, 10, 3, 5, 7 ]).
	"""

	if type(_range) is not str:
		for value in _range:
			if _isWithinBounds(value, minValue, maxValue):
				yield value

		return

	for _match in RANGE_ITEM_REGEX.finditer(_range):
		start, end, stride = _match.groups()

		if end is None:
			value = int(start, 0)

			if _isWithinBounds(value, minValue, maxValue):
				yield value
		else:
			_start, _end = int(start, 0), int(end, 0)

			yield from range(
				(_start if minValue is None else max(minValue, _start)),
				(_end   if maxValue is None else min(maxValue, _end)) + 1,
				1       if stride   is None else int(stride, 0)
			)

def isWithinRange(value, _range):
	"""
	Parses a string containing space-delimited positive integers, optionally
	with dashes specifying ranges and colons prefixing strides (e.g.
	"1 8-10 3-7:2") and checks whether the given value is within the range.
	"""

	if type(_range) is not str:
		return (value in _range)

	for _match in RANGE_ITEM_REGEX.finditer(_range):
		start, end, stride = _match.groups()

		if end is None:
			if value == int(start, 0):
				return True
		else:
			_start, _end = int(start, 0), int(end, 0)

			if value >= _start and value <= _end:
				if stride is None:
					return True
				if not ((value - _start) % int(stride, 0)):
					return True

	return False

def _parseKeyValue(strings, constructor = dict, separator = "="):
	obj = constructor()

	for item in strings:
		if not item.strip():
			continue

		key, value = item.split(separator, 1)
		key, value = key.strip(), value.strip()

		if value.startswith(( "\"", "'" )):
			value = literal_eval(value)

		obj[key] = value

	return obj

## Data structure utilities

def bestHashTableLength(hashes, minLoadFactor = 0.7, chainPenalty = 0.5):
	"""
	Takes a sequence of integer hash values and simulates hash table packing
	repeatedly with different table lengths (number of buckets). A score is
	calculated for each length, based on how many buckets went unused and how
	many entries are chained to buckets. Returns the hash table length
	associated with the best score.
	"""

	length    = len(hashes)
	bestValue = None
	bestScore = 1e10

	for numBuckets in range(length, round(length / minLoadFactor + 0.5)):
		table   = ( _hash % numBuckets for _hash in hashes )
		chained = numBuckets - len(set(table))

		if (score := numBuckets + chained * chainPenalty) < bestScore:
			bestValue = numBuckets
			bestScore = score

	return bestValue

## Path utilities

def iteratePaths(paths):
	if type(paths) is str or isinstance(paths, Path):
		yield Path(paths)
	else:
		for path in paths:
			yield Path(path)

## Text file parsing

COMMENT_REGEX = {
	"shell":  re.compile(r"((?:\".*\"|'.*'|[^\"'])*?)(?:\#.*)?$", re.MULTILINE),
	"python": re.compile(r"((?:\".*\"|'.*'|[^\"'])*?)(?:(?:\#.*)?$|\"\"\"(?:.|\n)*?\"\"\"|'''(?:.|\n)*?''')", re.MULTILINE),
	"js":     re.compile(r"((?:\".*\"|'.*'|[^\"'])*?)(?:(?:\/\/.*)?$|\/\*(?:.|\n)*?\*\/)", re.MULTILINE)
}

def parseText(text, commentMode = "shell"):
	"""
	Returns the given text with all comments stripped out.
	"""

	regex = COMMENT_REGEX[commentMode.lower()]

	text = text.replace("\r\n", "\n")
	text = "".join(regex.findall(text))
	text = text.replace("\0", "")

	return text

def parseJSON(text, *a, **k):
	return json.loads(parseText(text, "js"), *a, **k)

def parseKeyValue(text, constructor = dict, separator = "="):
	strings = parseText(text, "shell").splitlines()

	return _parseKeyValue(strings, constructor, separator)

## Case-insensitive dictionary

def _normalizeKey(key):
	return key.strip().lower() if (type(key) is str) else key

class CaseDict(UserDict):
	"""
	A dictionary with stripped, case-insensitive key collation. The case of the
	last key used to set an item is preserved.
	"""

	def __setitem__(self, key, value):
		self.data[_normalizeKey(key)] = key, value

	def __getitem__(self, key):
		return self.data[_normalizeKey(key)][1]

	def __delitem__(self, key):
		del self.data[_normalizeKey(key)]

	def __contains__(self, key):
		return (_normalizeKey(key) in self.data)

	def __iter__(self):
		return (key for key, value in self.data.values())

	#def __repr__(self):
		#return f"CaseDict({repr(dict(self.data.values()))})"

	def values(self):
		return (value for key, value in self.data.values())

	def items(self):
		return self.data.values()

## Persistent cache directory

CACHE_DIR_PREFIX = "psxfudgecache_"

class CacheDirectory:
	"""
	A class for managing a cache, i.e. a directory containing temporary files
	that can be accessed through unique identifier strings. This class is
	stateless to make parallelization easier.
	"""

	def __init__(self, path = None, prefixBits = 4):
		if path:
			self.path = Path(path)
			self.path.mkdir(parents = True, exist_ok = True)
		else:
			self.path = Path(mkdtemp("", CACHE_DIR_PREFIX))

		self.prefixBits = prefixBits

	def prepare(self):
		for prefix in range(2 ** self.prefixBits):
			path = self.path.joinpath(f"{prefix:02x}")
			path.mkdir(parents = True, exist_ok = True)

	def getPath(self, name):
		_hash  = hash32(name)
		prefix = _hash >> (32 - self.prefixBits)
		path   = self.path.joinpath(f"{prefix:02x}")

		path.mkdir(parents = True, exist_ok = True)
		return path.joinpath(f"{_hash:08x}.bin")

	def lastModified(self, name):
		path = self.getPath(name)
		if not path.is_file():
			return 0

		return path.stat().st_mtime

	def delete(self, name):
		path = self.getPath(name)
		if path.is_file():
			path.unlink()
