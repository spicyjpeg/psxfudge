# -*- coding: utf-8 -*-
# (C) 2022 spicyjpeg

import os, re, math, logging, json
from time      import gmtime
from itertools import chain
from ast       import literal_eval
from struct    import Struct
from glob      import iglob
from tempfile  import mkdtemp
from argparse  import ArgumentParser

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

def parseRange(_range, separator = "-"):
	"""
	Parses a string containing space-delimited integers, optionally with dashes
	indicating ranges (e.g. "1 7 3-5", which translates to [ 1, 7, 3, 4, 5 ])
	and yields all values.
	"""

	for item in _range.split(" "):
		if not item:
			continue

		if separator in item:
			start, end = item.split(separator)
			yield from range(
				int(start, 0),
				int(end, 0)
			)

		yield int(item, 0)

def isWithinRange(value, _range, separator = "-"):
	"""
	Parses a string containing space-delimited integers, optionally with dashes
	indicating ranges (e.g. "1 7 3-5") and checks whether the given value is
	listed.
	"""

	for item in _range.split(" "):
		if not item:
			continue

		if separator in item:
			start, end = item.split(separator)
			if value >= int(start, 0) and value <= int(end, 0):
				return True

		if value == int(item, 0):
			return True

	return False

def _parseKeyValue(strings, lowerCase = False, separator = "="):
	"""
	Takes a list of "key=value" strings and returns a dict. All keys can
	optionally be transformed to lower case.
	"""

	options = {}

	for item in strings:
		if not item.strip():
			continue

		key, value = item.split(separator, 1)
		value      = value.strip()

		if lowerCase:
			key = key.lower()
		if value.startswith(( "\"", "'" )):
			value = literal_eval(value)

		options[key.strip()] = value

	return options

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

def globPaths(paths):
	if type(paths) is str:
		yield from iglob(paths)
		return

	for path in paths:
		yield from iglob(path)

## Text file parsing

COMMENT_REGEX = {
	"shell":  re.compile(r"((?:\".*\"|'.*'|[^\"'])*?)(?:\#.*)?$", re.MULTILINE),
	"python": re.compile(r"((?:\".*\"|'.*'|[^\"'])*?)(?:(?:\#.*)?$|\"\"\"(?:.|\n)*?\"\"\"|'''(?:.|\n)*?''')", re.MULTILINE),
	#"js":     re.compile(r"((?:\".*\"|'.*'|[^\"'])*?)(?:\/\/.*)?$"),
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

def parseKeyValue(text, lowerCase = False, separator = "="):
	strings = parseText(text, "shell").splitlines()

	return _parseKeyValue(strings, lowerCase, separator)

## Command line argument parser

class ArgParser(ArgumentParser):
	"""
	An enhanced subclass of argparse.ArgumentParser that automatically sets up
	logging and a few common options.
	"""

	def __init__(self, description):
		super().__init__(
			description = description,
			epilog      = "This script is part of the PSXFudge toolkit.",
			add_help    = False
			#fromfile_prefix_chars = "@"
		)

		group = self.add_argument_group("Tool options")
		group.add_argument(
			"-h", "--help",
			action = "help",
			help   = "Show this help message and exit"
		)
		group.add_argument(
			"-v", "--verbose",
			action = "count",
			help   = "Increase logging verbosity (-v = info, -vv = info + debug)"
		)

	def parse_args(self, args = None):
		args = super().parse_args(args)

		logging.basicConfig(
			format = "[%(funcName)-13s %(levelname)-7s] %(message)s",
			level  = (
				logging.WARNING,
				logging.INFO,    # -v
				logging.DEBUG    # -vv
			)[min(args.verbose or 0, 2)]
		)

		return args

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
			if not os.path.isdir(path):
				os.mkdir(path)

			self.path = path
		else:
			self.path = mkdtemp("", CACHE_DIR_PREFIX)

		self.prefixBits = prefixBits

	def prepare(self):
		for prefix in range(2 ** self.prefixBits):
			path = os.path.join(self.path, f"{prefix:02x}")

			if not os.path.isdir(path):
				os.mkdir(path)

	def getPath(self, name):
		_hash  = hash32(name)
		prefix = _hash >> (32 - self.prefixBits)
		path   = os.path.join(self.path, f"{prefix:02x}")

		if not os.path.isdir(path):
			os.mkdir(path)

		return os.path.join(path, f"{_hash:08x}.bin")

	def lastModified(self, name):
		path = self.getPath(name)
		if not os.path.isfile(path):
			return 0

		return os.stat(path).st_mtime

	def open(self, name, *args, **namedArgs):
		return open(self.getPath(name), *args, **namedArgs)

	def delete(self, name):
		path = self.getPath(name)
		if os.path.isfile(path):
			os.remove(path)
